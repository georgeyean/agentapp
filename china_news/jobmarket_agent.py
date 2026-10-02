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
    {"name": "Harvard University",      "short": "Harvard",   "url": "https://gov.harvard.edu/job-market",
     "search": "Harvard Government department job market candidates political science"},
    {"name": "Princeton University",    "short": "Princeton", "url": "https://politics.princeton.edu/graduate/job-market-candidates",
     "search": "Princeton politics department job market candidates political science"},
    {"name": "Stanford University",     "short": "Stanford",  "url": "https://politicalscience.stanford.edu/graduate-program/job-market",
     "search": "Stanford political science department job market candidates"},
    {"name": "MIT",                     "short": "MIT",       "url": "https://polisci.mit.edu/job-market",
     "search": "MIT political science department job market candidates"},
    {"name": "University of Michigan",  "short": "Michigan",  "url": "https://lsa.umich.edu/polisci/graduates/job-market-candidates.html",
     "search": "University Michigan political science job market candidates"},
    {"name": "UC Berkeley",             "short": "Berkeley",  "url": "https://polisci.berkeley.edu/graduate/job-market",
     "search": "UC Berkeley political science job market candidates"},
    {"name": "Yale University",         "short": "Yale",      "url": "https://politicalscience.yale.edu/graduate/job-market-candidates",
     "search": "Yale political science department job market candidates"},
    {"name": "Columbia University",     "short": "Columbia",  "url": "https://polisci.columbia.edu/content/job-market",
     "search": "Columbia political science department job market candidates"},
    {"name": "University of Chicago",   "short": "UChicago",  "url": "https://political-science.uchicago.edu/job-market",
     "search": "University Chicago political science job market candidates"},
    {"name": "NYU",                     "short": "NYU",       "url": "https://as.nyu.edu/departments/politics/graduate-program/job-market.html",
     "search": "NYU politics department job market candidates political science"},
    {"name": "UC San Diego",            "short": "UCSD",      "url": "https://polisci.ucsd.edu/graduate/job-market/index.html",
     "search": "UC San Diego political science job market candidates"},
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

def fetch_page(url):
    """Fetch a page and return (text, links_map) where links_map is {anchor_text: href}."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        if not HAS_BS4:
            text = re.sub(r"<[^>]+>", " ", resp.text)
            return text, {}
        soup = BeautifulSoup(resp.text, "html.parser")
        # Collect all links before stripping tags
        links = {}
        for a in soup.find_all("a", href=True):
            anchor = a.get_text(strip=True)
            href = a["href"]
            if anchor and href and not href.startswith("#") and not href.startswith("mailto:"):
                # Make relative URLs absolute
                if href.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                links[anchor] = href
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text, links
    except Exception as e:
        print(f"    Fetch failed ({url}): {e}")
        return None, {}


def _ddg_search(query):
    """Run a DuckDuckGo HTML search and return decoded result URLs."""
    from urllib.parse import parse_qs, urlparse, unquote
    try:
        resp = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": query, "kl": "us-en"},
            headers=HEADERS, timeout=15,
        )
        if not HAS_BS4:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        urls = []
        for result in soup.select(".result__url, .result__a"):
            href = result.get("href", "") or result.get_text(strip=True)
            if "uddg=" in href:
                qs = parse_qs(urlparse(href).query)
                href = unquote(qs.get("uddg", [""])[0])
            if href.startswith("http"):
                urls.append(href)
        return urls
    except Exception as e:
        print(f"    DuckDuckGo search failed: {e}")
        return []


def search_department_url(query):
    """Search DuckDuckGo for the department's job market page URL."""
    for href in _ddg_search(query):
        if "job-market" in href or "job_market" in href or "jobmarket" in href:
            return href
    return None


SKIP_SITE_DOMAINS = [
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "wikipedia.org", "researchgate.net", "semanticscholar.org",
    "jstor.org", "ssrn.com",
]
SKIP_SITE_PATHS = [
    "/directory/", "/people/hire", "job-market", "job_market",
    "/graduate/", "/faculty/", "/grad-students/", "/hire",
]
PERSONAL_HOSTS = [
    "github.io", "sites.google.com", "scholars.harvard", "scholars.",
    "wordpress.com", "wixsite.com", "squarespace.com", "weebly.com",
    "notion.site", "strikingly.com",
]


def _looks_like_personal_site(href, name):
    """True if href looks like a personal academic website (not a dept/social page)."""
    h = href.lower()
    if any(d in h for d in SKIP_SITE_DOMAINS):
        return False
    if any(p in h for p in SKIP_SITE_PATHS):
        return False
    parts = name.lower().split()
    first, last = parts[0], parts[-1]
    has_name = (first in h or last in h
                or (first[0] + last) in h or (first + last) in h)
    is_personal_host = any(ph in h for ph in PERSONAL_HOSTS)
    return has_name or is_personal_host


def find_site_in_dept_links(name, dept_links):
    """Find a candidate's personal site URL from the department page link map."""
    for anchor, href in dept_links.items():
        if name.lower() not in anchor.lower():
            continue
        if _looks_like_personal_site(href, name):
            return href
        # Directory or dept page — follow it and look for an external personal link
        dir_text, dir_links = fetch_page(href)
        for a_text, a_href in dir_links.items():
            if _looks_like_personal_site(a_href, name):
                return a_href
    return None


def search_personal_site_bing(name, school):
    """Bing fallback: search for a candidate's personal website."""
    time.sleep(3)
    parts = name.split()
    # Quote last name + first to avoid "Can" being parsed as a modal verb
    name_q = f'"{parts[-1]}" "{parts[0]}"' if len(parts) > 1 else f'"{name}"'
    query = f'{name_q} political science {school} academic website'
    try:
        headers = {**HEADERS, "Accept-Language": "en-US,en;q=0.9"}
        resp = requests.get("https://www.bing.com/search", params={"q": query}, headers=headers, timeout=15)
        from bs4 import BeautifulSoup as _BS
        soup = _BS(resp.text, "html.parser")
        for a in soup.select("li.b_algo h2 a"):
            href = a.get("href", "")
            if _looks_like_personal_site(href, name):
                return href
    except Exception as e:
        print(f"    Bing search failed: {e}")
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

def extract_candidates(school_name, page_text, links):
    prompt = f"""This is the job market page for the {school_name} Political Science department.

Extract all PhD job market candidates. For each return:
- name: full name
- subfield: classify into exactly one of: IR, CP, AP, Theory, Methods — based on their dissertation/research. {SUBFIELD_DESCRIPTIONS}
- dissertation: dissertation title or research focus (brief)
- advisor: advisor name(s), or ""

Return a JSON array. If no candidates found, return [].

Page text:
{page_text[:6000]}"""

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
    """Returns { pub_count, journals, wp_count } — summary only, no titles."""
    prompt = f"""From this academic website of {name} (Political Science PhD candidate):

1. Count how many journal publications or forthcoming articles they have (peer-reviewed only, not working papers).
2. List the journal/venue abbreviations (e.g. APSR, JOP, IO, AJPS, World Politics).
3. Count how many working papers or papers under review they have.

Return ONLY this JSON (no other text):
{{"pub_count": 0, "journals": [], "wp_count": 0}}

Page text:
{page_text[:6000]}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        return {
            "pub_count": int(result.get("pub_count", 0)),
            "journals":  result.get("journals", []),
            "wp_count":  int(result.get("wp_count", 0)),
        }
    except openai.RateLimitError as e:
        if "credit_balance_exhausted" in str(e):
            _send_credit_alert("Job Market Agent (jobmarket_agent.py)")
        print(f"    GPT rate limit: {e}")
        return {"pub_count": 0, "journals": [], "wp_count": 0}
    except Exception as e:
        print(f"    GPT failed for {name}: {e}")
        return {"pub_count": 0, "journals": [], "wp_count": 0}


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

    pub_count = c.get("pub_count", 0)
    journals  = c.get("journals", [])
    wp_count  = c.get("wp_count", 0)
    journals_str = f" ({', '.join(journals)})" if journals else ""
    pub_str = f"{pub_count} pub{'s' if pub_count != 1 else ''}{journals_str}"
    wp_str  = f"{wp_count} WP"
    summary = f"{pub_str} &nbsp;·&nbsp; {wp_str}"

    html = f"""
<div style="margin-bottom:12px;padding:10px 12px;background:#fafafa;border-radius:6px;border-left:3px solid {color};">
  <div style="margin-bottom:3px;">{name_html}{school_tag}</div>
  <p style="font-size:11px;color:#888;margin:0 0 4px;">{summary}</p>"""

    delta = c.get("delta")
    if delta:
        html += f'  <p style="font-size:10px;font-weight:700;color:#b45309;margin:0;">↑ {delta}</p>'

    html += "</div>"
    return html


def render_html(all_candidates_by_school):
    today = datetime.now().strftime("%B %d, %Y")
    by_sf = _flatten_by_subfield(all_candidates_by_school)
    total_updated = sum(1 for v in all_candidates_by_school.values() for c in v if c.get("delta"))
    total_cands = sum(len(v) for v in all_candidates_by_school.values())

    html = f"""<html><body style="margin:0;padding:0;background:#f0f0f0;">
<div style="max-width:640px;margin:0 auto;padding:16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<div style="background:#fff;border-radius:8px;overflow:hidden;color:#222;">
<div style="background:#1c1b18;padding:24px 20px;">
  <h1 style="margin:0 0 4px;font-size:20px;font-weight:700;color:#fff;">PoliSci Job Market</h1>
  <p style="margin:0;font-size:12px;color:#6b6962;">{today} &nbsp;·&nbsp; {total_cands} candidates &nbsp;·&nbsp; {total_updated} updated this week</p>
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
            pub_count = c.get("pub_count", 0)
            journals  = c.get("journals", [])
            wp_count  = c.get("wp_count", 0)
            journals_str = f" ({', '.join(journals)})" if journals else ""
            pub_str = f"{pub_count} pub{journals_str}"
            line = f"  {c['name']} · {c.get('school','')} — {pub_str} · {wp_count} WP"
            if c.get("site_url"):
                line += f"  {c['site_url']}"
            if c.get("delta"):
                line += f"  [↑ {c['delta']}]"
            lines.append(line)
    return "\n".join(lines)


def send_email(all_candidates_by_school):
    today = datetime.now().strftime("%Y-%m-%d")
    total_updated = sum(1 for v in all_candidates_by_school.values() for c in v if c.get("delta"))
    subject = f"PoliSci Job Market ({today})" + (f" — {total_updated} updated" if total_updated else "")
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

def _get_dept_url(school, seen):
    """Return the department page URL, using cached URL if available, else try primary, else DuckDuckGo."""
    short = school["short"]
    # Check for a previously discovered (and working) URL in the seen cache
    cached_url = seen.get("_dept_urls", {}).get(short)
    if cached_url:
        print(f"  Using cached dept URL: {cached_url}")
        return cached_url

    # Try the primary URL first
    primary = school["url"]
    try:
        resp = requests.get(primary, headers=HEADERS, timeout=15)
        if resp.status_code < 400:
            return primary
        print(f"  Primary URL returned {resp.status_code}, searching DuckDuckGo...")
    except Exception as e:
        print(f"  Primary URL failed ({e}), searching DuckDuckGo...")

    found = search_department_url(school["search"])
    if found:
        print(f"  Found via DuckDuckGo: {found}")
    return found


def main(dry_run=False):
    seen = load_seen()
    # seen: { "_dept_urls": { short: url }, school_short: { candidate_name: { site_url, pub_count, wp_count } } }
    all_by_school = {}

    for school in SCHOOLS:
        short = school["short"]
        school_name = school["name"]
        print(f"\n{school_name}")

        dept_url = _get_dept_url(school, seen)
        if not dept_url:
            print("  Skipping — could not find department page")
            continue

        page_text, links = fetch_page(dept_url)
        if not page_text:
            print("  Skipping — could not fetch department page")
            continue

        # Always cache discovered dept URL immediately
        seen.setdefault("_dept_urls", {})[short] = dept_url
        save_seen(seen)

        candidates = extract_candidates(school_name, page_text, links)
        print(f"  {len(candidates)} candidates on page")
        if not candidates:
            continue

        school_seen = seen.setdefault(short, {})
        school_rows = []

        for c in candidates:
            name = c.get("name", "").strip()
            if not name:
                continue

            prev = school_seen.get(name, {})

            # Use cached site_url; otherwise discover via dept links then Bing
            site_url = prev.get("site_url", "")
            print(f"  → {name}", end="")

            papers = {"pub_count": 0, "journals": [], "wp_count": 0}
            if not site_url:
                site_url = find_site_in_dept_links(name, links) or ""
                if site_url:
                    print(f" (dept link)", end="")
                else:
                    site_url = search_personal_site_bing(name, school_name) or ""
                    if site_url:
                        print(f" (Bing)", end="")

            if site_url:
                site_text, _ = fetch_page(site_url)
                if site_text:
                    papers = extract_papers(name, site_text)
                    print(f" — {papers['pub_count']} pubs · {papers['wp_count']} WPs", end="")
                time.sleep(1)
            else:
                print(" — no site found", end="")
            print()

            pub_count = papers["pub_count"]
            journals  = papers["journals"]
            wp_count  = papers["wp_count"]

            # Detect changes vs previous run
            prev_pub = prev.get("pub_count", 0)
            prev_wp  = prev.get("wp_count", 0)
            delta_parts = []
            if pub_count > prev_pub:
                delta_parts.append(f"+{pub_count - prev_pub} pub{'s' if pub_count - prev_pub != 1 else ''}")
            if wp_count > prev_wp:
                delta_parts.append(f"+{wp_count - prev_wp} WP")
            delta = ", ".join(delta_parts) if delta_parts else None
            if delta:
                print(f"    ↑ {delta}")

            school_rows.append({
                "name": name,
                "subfield": c.get("subfield", ""),
                "site_url": site_url,
                "pub_count": pub_count,
                "journals":  journals,
                "wp_count":  wp_count,
                "delta": delta,
            })

            # Always save URLs immediately so a crash mid-run doesn't lose discovered sites
            school_seen[name] = {
                "site_url":  site_url,
                "pub_count": pub_count if not dry_run else prev.get("pub_count", 0),
                "wp_count":  wp_count  if not dry_run else prev.get("wp_count", 0),
            }
            save_seen(seen)

            time.sleep(1)

        if school_rows:
            all_by_school[school_name] = school_rows

    if not all_by_school:
        print("\nNo candidates found — no email sent.")
        return

    total_updated = sum(1 for v in all_by_school.values() for c in v if c.get("delta"))
    total_cands = sum(len(v) for v in all_by_school.values())
    print(f"\n{total_cands} candidates · {total_updated} updated")

    if dry_run:
        print("\n--- DRY RUN ---")
        print(render_text(all_by_school))
        return

    send_email(all_by_school)
    print("Done.")


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
