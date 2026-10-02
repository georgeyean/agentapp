import requests
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import quote
from openai import OpenAI
import openai
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    print("Warning: beautifulsoup4 not installed. Run: pip install beautifulsoup4")

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GMAIL_USER = os.getenv("EMAIL_FROM", "georgeyean@gmail.com")
GMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = "georgeyean@gmail.com"

client = OpenAI(api_key=OPENAI_API_KEY)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(SCRIPT_DIR, "seen_jobmarket_candidates.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

SCHOOLS = [
    {"name": "Harvard University",                "short": "Harvard",      "url": "https://gov.harvard.edu/job-market"},
    {"name": "Princeton University",               "short": "Princeton",    "url": "https://politics.princeton.edu/job-market"},
    {"name": "Stanford University",                "short": "Stanford",     "url": "https://politicalscience.stanford.edu/graduate/job-market"},
    {"name": "MIT",                                "short": "MIT",          "url": "https://polisci.mit.edu/graduate/job-market"},
    {"name": "University of Michigan",             "short": "Michigan",     "url": "https://lsa.umich.edu/polisci/graduates/job-market.html"},
    {"name": "UC Berkeley",                        "short": "Berkeley",     "url": "https://polisci.berkeley.edu/graduate/job-market-candidates"},
    {"name": "Yale University",                    "short": "Yale",         "url": "https://politicalscience.yale.edu/graduate/job-market"},
    {"name": "Columbia University",                "short": "Columbia",     "url": "https://polisci.columbia.edu/graduate-program/job-market"},
    {"name": "University of Chicago",              "short": "UChicago",     "url": "https://political-science.uchicago.edu/graduate/job-market"},
    {"name": "NYU",                                "short": "NYU",          "url": "https://as.nyu.edu/departments/politics/graduate-program/job-market.html"},
    {"name": "UC San Diego",                       "short": "UCSD",         "url": "https://polisci.ucsd.edu/graduate/job-market/index.html"},
]


# ── Persistence ───────────────────────────────────────────────────────────────
# Structure: { "Harvard": { "Yuki Tanaka": { "site_url": "...", "papers": ["title1", ...] } } }

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)


# ── Scraping ──────────────────────────────────────────────────────────────────

def fetch_page_text(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        if not HAS_BS4:
            return re.sub(r"<[^>]+>", " ", resp.text)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        print(f"    Fetch failed ({url}): {e}")
        return None


# ── GPT: extract candidates from department page ──────────────────────────────

SUBFIELD_LABELS = ["IR", "CP", "AP", "Theory", "Methods"]
SUBFIELD_DESCRIPTIONS = (
    "IR = International Relations (war, diplomacy, trade, alliances, international organizations, foreign policy); "
    "CP = Comparative Politics (regime type, elections, parties, state capacity, ethnic conflict, non-US countries); "
    "AP = American Politics (US Congress, presidency, public opinion, courts, US elections, US parties); "
    "Theory = Political Theory or Philosophy (normative, justice, legitimacy, democratic theory); "
    "Methods = Formal models, causal inference, measurement, experiments, statistical methods as the primary contribution."
)

def extract_candidates(school_name, page_text):
    prompt = f"""This is the job market page for the {school_name} Political Science department.

Extract all PhD job market candidates. For each return:
- name: full name
- subfield: classify into exactly one of: IR, CP, AP, Theory, Methods — based on their dissertation/research. {SUBFIELD_DESCRIPTIONS}
- dissertation: dissertation title or research focus
- advisor: advisor name(s), or ""
- site_url: URL to their personal website or CV page, or ""

Return a JSON array. If no candidates found, return [].

Page text:
{page_text[:8000]}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except openai.RateLimitError as e:
        if "credit_balance_exhausted" in str(e):
            _send_credit_alert("Job Market Agent (jobmarket_agent.py)")
        print(f"    GPT rate limit: {e}")
        return []
    except Exception as e:
        print(f"    GPT failed for {school_name}: {e}")
        return []


# ── GPT: extract papers from personal website ─────────────────────────────────

def extract_papers(name, page_text):
    """Returns { publications: [{title, venue, url}], working_papers: [{title, url}] }"""
    prompt = f"""This is the personal academic website of {name}, a PhD candidate in Political Science.

Extract their papers in two categories:

1. publications: papers published in a journal or forthcoming. For each: title, venue (journal name), url (link to paper if present, else "")
2. working_papers: working papers, papers under review, or draft papers. For each: title, url (link if present, else "")

Return JSON in this exact format:
{{"publications": [{{"title": "...", "venue": "...", "url": ""}}], "working_papers": [{{"title": "...", "url": ""}}]}}

If none found in a category, return an empty list for it.

Page text:
{page_text[:6000]}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1200,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        return {
            "publications": result.get("publications", []),
            "working_papers": result.get("working_papers", []),
        }
    except openai.RateLimitError as e:
        if "credit_balance_exhausted" in str(e):
            _send_credit_alert("Job Market Agent (jobmarket_agent.py)")
        print(f"    GPT rate limit: {e}")
        return {"publications": [], "working_papers": []}
    except Exception as e:
        print(f"    GPT failed for {name}: {e}")
        return {"publications": [], "working_papers": []}


# ── SMTP / email helpers ──────────────────────────────────────────────────────

def _smtp_connection():
    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20)
        server.login(GMAIL_USER, GMAIL_PASS)
        return server
    except Exception as e:
        print(f"  Port 465 failed ({e}), trying port 587...")
    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=20)
    server.ehlo(); server.starttls(); server.ehlo()
    server.login(GMAIL_USER, GMAIL_PASS)
    return server


def _send_credit_alert(agent_name):
    try:
        html = f"""<div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#c0392b;">OpenAI Credits Exhausted</h2>
<p><strong>Agent:</strong> {agent_name}</p>
<p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<p>Top up at <a href="https://platform.openai.com/settings/organization/billing">platform.openai.com</a>.</p>
</div>"""
        msg = MIMEMultipart("alternative")
        msg["From"] = f"Agent Alert <{GMAIL_USER}>"
        msg["To"] = EMAIL_TO
        msg["Subject"] = f"⚠️ OpenAI Credits Exhausted — {datetime.now().strftime('%Y-%m-%d')}"
        msg.attach(MIMEText(html, "html", "utf-8"))
        with _smtp_connection() as server:
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send credit alert: {e}")


# ── Email rendering ───────────────────────────────────────────────────────────

SF_META = {
    "IR":      {"color": "#15803d", "label": "International Relations"},
    "CP":      {"color": "#2563eb", "label": "Comparative Politics"},
    "AP":      {"color": "#b91c1c", "label": "American Politics"},
    "Theory":  {"color": "#6d28d9", "label": "Political Theory"},
    "Methods": {"color": "#b45309", "label": "Methods"},
}
SF_ORDER = ["IR", "CP", "AP", "Theory", "Methods"]

def _sf_color(sf):
    return SF_META.get(sf, {}).get("color", "#374151")


def _flatten_by_subfield(all_candidates_by_school):
    """Regroup all candidates into { subfield: [ {**cand, school} ] }"""
    by_sf = {sf: [] for sf in SF_ORDER}
    other = []
    for school, candidates in all_candidates_by_school.items():
        for c in candidates:
            sf = c.get("subfield", "")
            entry = {**c, "school": school}
            if sf in by_sf:
                by_sf[sf].append(entry)
            else:
                other.append(entry)
    if other:
        by_sf["Other"] = other
    return {sf: v for sf, v in by_sf.items() if v}


def _candidate_card_html(c, show_school=True):
    sf = c.get("subfield", "")
    color = _sf_color(sf)
    site_url = c.get("site_url", "")
    name_html = (
        f'<a href="{site_url}" style="font-weight:700;font-size:14px;color:#1c1b18;text-decoration:none;">{c["name"]}</a>'
        if site_url else
        f'<span style="font-weight:700;font-size:14px;">{c["name"]}</span>'
    )
    school_tag = f'<span style="font-size:11px;color:#888;margin-left:6px;">{c.get("school","")}</span>' if show_school else ""

    pubs = c.get("publications", [])
    wps  = c.get("working_papers", [])
    journals = ", ".join(p["venue"] for p in pubs if p.get("venue"))
    pub_str = f"{len(pubs)} pub{'s' if len(pubs)!=1 else ''}" + (f" ({journals})" if journals else "")
    wp_str  = f"{len(wps)} WP"
    summary = f"{pub_str} &nbsp;·&nbsp; {wp_str}" if (pubs or wps) else "No papers listed"

    html = f"""
<div style="margin-bottom:12px;padding:10px 12px;background:#fafafa;border-radius:6px;border-left:3px solid {color};">
  <div style="margin-bottom:3px;">{name_html}{school_tag}</div>
  <p style="font-size:11px;color:#888;margin:0 0 6px;">{summary}</p>"""

    if c.get("new_papers"):
        html += """  <div style="border-top:1px solid #eee;padding-top:6px;">
    <div style="font-size:10px;font-weight:700;color:#b45309;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px;">New this week</div>"""
        for p in c["new_papers"]:
            title_html = (
                f'<a href="{p["url"]}" style="color:#1c1b18;text-decoration:none;">{p["title"]}</a>'
                if p.get("url") else p["title"]
            )
            venue = f' <span style="color:#888;">— {p["venue"]}</span>' if p.get("venue") else ""
            status = (p.get("status") or "").lower()
            sc = "#15803d" if "publish" in status or "forthcoming" in status else "#6b7280"
            html += f"""
    <div style="margin-bottom:5px;padding:5px 8px;background:#fffbf0;border:1px solid #fed7aa;border-radius:4px;">
      <div style="font-size:12px;margin-bottom:1px;">{title_html}{venue}</div>
      <span style="font-size:10px;font-weight:600;color:{sc};">{status.upper()}</span>
    </div>"""
        html += "  </div>"

    html += "</div>"
    return html


def render_html(all_candidates_by_school):
    today = datetime.now().strftime("%B %d, %Y")
    by_sf = _flatten_by_subfield(all_candidates_by_school)
    total_new = sum(len(c["new_papers"]) for v in all_candidates_by_school.values() for c in v)
    total_cands = sum(len(v) for v in all_candidates_by_school.values())

    html = f"""<html><body style="margin:0;padding:0;background:#f0f0f0;">
<div style="max-width:640px;margin:0 auto;padding:16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="background:#fff;border-radius:8px;overflow:hidden;color:#222;">
<div style="background:#1c1b18;padding:24px 20px;">
  <h1 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#fff;">PoliSci Job Market</h1>
  <p style="margin:0;font-size:12px;color:#6b6962;">{today} &nbsp;·&nbsp; {total_cands} candidates &nbsp;·&nbsp; {total_new} new paper{'s' if total_new!=1 else ''} this week</p>
</div>
<div style="padding:20px;">"""

    for sf, candidates in by_sf.items():
        meta = SF_META.get(sf, {"color": "#374151", "label": sf})
        color = meta["color"]
        label = meta["label"]
        html += f"""
<div style="margin-bottom:28px;">
  <div style="display:flex;align-items:center;gap:10px;padding-bottom:8px;border-bottom:2px solid {color};margin-bottom:12px;">
    <span style="display:inline-block;font-size:12px;font-weight:700;color:#fff;background:{color};padding:2px 10px;border-radius:4px;letter-spacing:.3px;">{sf}</span>
    <span style="font-size:13px;color:#555;">{label}</span>
    <span style="font-size:12px;color:#aaa;margin-left:auto;">{len(candidates)} candidate{'s' if len(candidates)!=1 else ''}</span>
  </div>"""
        for c in candidates:
            html += _candidate_card_html(c, show_school=True)
        html += "</div>"

    html += """</div>
<div style="background:#f8f8f8;padding:12px 20px;text-align:center;border-top:1px solid #eee;">
  <p style="margin:0;font-size:11px;color:#999;">PoliSci Job Market · Top 20 US News departments</p>
</div>
</div></div></body></html>"""
    return html


def render_text(all_candidates_by_school):
    today = datetime.now().strftime("%Y-%m-%d")
    by_sf = _flatten_by_subfield(all_candidates_by_school)
    lines = [f"PoliSci Job Market — {today}\n"]
    for sf, candidates in by_sf.items():
        label = SF_META.get(sf, {}).get("label", sf)
        lines.append(f"\n[{sf}] {label} ({len(candidates)})")
        lines.append("=" * 40)
        for c in candidates:
            pubs = c.get("publications", [])
            wps  = c.get("working_papers", [])
            journals = ", ".join(p["venue"] for p in pubs if p.get("venue"))
            pub_str = f"{len(pubs)} pub" + (f" ({journals})" if journals else "")
            lines.append(f"  {c['name']} · {c.get('school','')} — {pub_str} · {len(wps)} WP")
            for p in c["new_papers"]:
                venue = f" [{p['venue']}]" if p.get("venue") else ""
                url = f" {p['url']}" if p.get("url") else ""
                lines.append(f"    NEW: {p['title']}{venue}{url}")
    return "\n".join(lines)


def send_email(all_candidates_by_school):
    today = datetime.now().strftime("%Y-%m-%d")
    total_new = sum(len(c["new_papers"]) for v in all_candidates_by_school.values() for c in v)
    subject = f"PoliSci Job Market ({today})" + (f" — {total_new} new paper{'s' if total_new!=1 else ''}" if total_new else "")
    with _smtp_connection() as server:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"PoliSci Job Market <{GMAIL_USER}>"
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(render_text(all_candidates_by_school), "plain", "utf-8"))
        msg.attach(MIMEText(render_html(all_candidates_by_school), "html", "utf-8"))
        server.send_message(msg)
    print(f"Email sent to {EMAIL_TO}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run=False):
    seen = load_seen()
    # seen: { school_short: { candidate_name: { site_url, pub_titles: [], wp_titles: [] } } }
    all_by_school = {}  # school_name -> list of candidate dicts for email

    for school in SCHOOLS:
        short = school["short"]
        school_name = school["name"]
        print(f"\n{school_name}")

        page_text = fetch_page_text(school["url"])
        if not page_text:
            print("  Skipping — could not fetch department page")
            continue

        candidates = extract_candidates(school_name, page_text)
        print(f"  {len(candidates)} candidates on page")
        if not candidates:
            continue

        school_seen = seen.setdefault(short, {})
        school_rows = []

        for c in candidates:
            name = c.get("name", "").strip()
            if not name:
                continue
            site_url = c.get("site_url", "")
            print(f"  → {name}", end="")

            papers = {"publications": [], "working_papers": []}
            if site_url:
                site_text = fetch_page_text(site_url)
                if site_text:
                    papers = extract_papers(name, site_text)
                    n = len(papers["publications"]) + len(papers["working_papers"])
                    print(f" — {n} papers", end="")
                time.sleep(1)
            else:
                print(f" — no site URL", end="")
            print()

            pubs = papers["publications"]
            wps = papers["working_papers"]

            prev = school_seen.get(name, {})
            seen_pub_titles = set(prev.get("pub_titles", []))
            seen_wp_titles  = set(prev.get("wp_titles", []))

            new_papers = []
            for p in pubs:
                if p["title"] not in seen_pub_titles:
                    new_papers.append({**p, "status": "published"})
            for p in wps:
                if p["title"] not in seen_wp_titles:
                    new_papers.append({**p, "status": "working paper"})

            if new_papers:
                print(f"    {len(new_papers)} new: {', '.join(p['title'][:50] for p in new_papers)}")

            school_rows.append({
                "name": name,
                "subfield": c.get("subfield", ""),
                "site_url": site_url,
                "publications": pubs,
                "working_papers": wps,
                "new_papers": new_papers,
            })

            if not dry_run:
                school_seen[name] = {
                    "site_url": site_url,
                    "pub_titles": list({p["title"] for p in pubs} | seen_pub_titles),
                    "wp_titles":  list({p["title"] for p in wps}  | seen_wp_titles),
                }

            time.sleep(1)

        if school_rows:
            all_by_school[school_name] = school_rows

    if not all_by_school:
        print("\nNo candidates found — no email sent.")
        return

    total_new = sum(len(c["new_papers"]) for v in all_by_school.values() for c in v)
    total_cands = sum(len(v) for v in all_by_school.values())
    print(f"\n{total_cands} candidates · {total_new} new papers")

    if dry_run:
        print("\n--- DRY RUN ---")
        print(render_text(all_by_school))
        return

    send_email(all_by_school)
    save_seen(seen)
    print("Done.")


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
