import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from openai import OpenAI
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import os
import re
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GMAIL_USER = os.getenv("EMAIL_FROM", "georgeyean@gmail.com")
GMAIL_PASS = os.getenv("EMAIL_PASS")

client = OpenAI(api_key=OPENAI_API_KEY)

# ── Profile ──────────────────────────────────────────────────────────────
PROFILE = {
    "name": "George Yean",
    "stage": "PhD student (ABD, about to write dissertation)",
    "institution": "Harvard University, Department of Government",
    "citizenship": "Canadian",
    "subfields": [
        "International Relations",
        "International Security",
        "Chinese Political Economy",
        "Chinese Foreign Policy",
        "International Political Economy (IPE)",
    ],
    "keywords": [
        "China", "security", "foreign policy", "political economy",
        "international relations", "IR", "IPE", "Asia", "Indo-Pacific",
        "great power", "defense", "diplomacy", "trade", "geopolitics",
        "dissertation", "ABD", "PhD", "graduate",
    ],
}

# ── Sources to scrape ────────────────────────────────────────────────────
# Each source: (name, url, type)
# type: "rss" = RSS feed, "web" = HTML scrape

FELLOWSHIP_SOURCES = [
    # RSS feeds
    ("H-Net Job Guide", "https://networks.h-net.org/h-announce/rss", "rss"),

    # Web pages to scrape
    ("SSRC Fellowships", "https://www.ssrc.org/fellowships/", "web"),
    ("ACLS Competitions", "https://www.acls.org/competitions/", "web"),
    ("USIP Grants & Fellowships", "https://www.usip.org/grants-fellowships", "web"),
    ("Wilson Center Fellowships", "https://www.wilsoncenter.org/fellowships-grants", "web"),
    ("CFR Fellowships", "https://www.cfr.org/fellowships", "web"),
    ("Brookings Fellowships", "https://www.brookings.edu/careers/fellowship-programs/", "web"),
    ("Carnegie Endowment", "https://carnegieendowment.org/about/jr-fellows", "web"),
    ("Smith Richardson Foundation", "https://www.srf.org/programs/international-security-foreign-policy/", "web"),
    ("Minerva Research Initiative", "https://minerva.defense.gov/Funding-Opportunities/", "web"),

    # Canada-specific
    ("SSHRC Doctoral Fellowships", "https://www.sshrc-crsh.gc.ca/funding-financement/programs-programmes/fellowships/doctoral-doctorat-eng.aspx", "web"),
    ("Trudeau Foundation Scholarships", "https://www.trudeaufoundation.ca/our-community/scholarships", "web"),
    ("Killam Fellowships", "https://www.killamlaureates.ca/killam-programs/killam-fellowships/", "web"),

    # Additional IR/Security
    ("Belfer Center Fellowships", "https://www.belfercenter.org/fellowships", "web"),
    ("CSIS Fellowships", "https://www.csis.org/programs/about-us/internships-and-fellowships", "web"),
    ("RAND Graduate Fellowship", "https://www.rand.org/about/edu_op/fellowships.html", "web"),
    ("Stimson Center", "https://www.stimson.org/careers/", "web"),
    ("East-West Center", "https://www.eastwestcenter.org/education", "web"),
    ("Fulbright Canada", "https://www.fulbright.ca/programs/canadian-students", "web"),
    ("Vanier CGS", "https://vanier.gc.ca/en/home-accueil.html", "web"),
    ("IISS Research Fellowships", "https://www.iiss.org/about-us/careers", "web"),

    # Harvard centers
    ("Weatherhead Center Harvard", "https://wcfia.harvard.edu/funding", "web"),
    ("Fairbank Center Harvard", "https://fairbank.fas.harvard.edu/grants-fellowships/", "web"),
    ("Asia Center Harvard", "https://asiacenter.harvard.edu/grants-fellowships", "web"),

    # Taiwan/China foundations
    ("Chiang Ching-kuo Foundation", "https://www.cckf.org/en/programs", "web"),

    # Canada government funding
    ("Canada Council for the Arts", "https://canadacouncil.ca/funding", "web"),
    ("IDRC (Intl Development Research Centre)", "https://idrc.ca/en/funding", "web"),
    ("Global Affairs Canada Scholarships", "https://www.educanada.ca/scholarships-bourses/index.aspx?lang=eng", "web"),

    # Professional associations
    ("APSA (American Political Science Association)", "https://www.apsanet.org/RESOURCES/Funding-Opportunities", "web"),
    ("MPSA (Midwest Political Science Association)", "https://www.mpsanet.org/awards/", "web"),
    ("ISA (International Studies Association)", "https://www.isanet.org/Programs/Awards", "web"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def fetch_rss_entries(name, url):
    """Fetch entries from an RSS feed."""
    entries = []
    try:
        feed = feedparser.parse(url)
        cutoff = datetime.utcnow() - timedelta(days=7)

        for entry in feed.entries[:50]:
            pub = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub = datetime(*entry.published_parsed[:6])
            if pub and pub < cutoff:
                continue

            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()[:500]
            link = entry.get("link", "")

            # Quick keyword pre-filter
            text = f"{title} {summary}".lower()
            if any(kw in text for kw in ["fellowship", "grant", "fund", "scholar",
                                          "dissertation", "doctoral", "postdoc",
                                          "political", "security", "china", "asia",
                                          "international", "foreign policy"]):
                entries.append({
                    "source": name,
                    "title": title,
                    "description": summary,
                    "link": link,
                })
    except Exception as e:
        print(f"  RSS error ({name}): {e}")

    return entries


def fetch_web_page(name, url):
    """Scrape a web page for fellowship/grant text."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove scripts and styles
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # Get all text content
        text = soup.get_text(separator="\n", strip=True)

        # Truncate to reasonable size
        text = text[:5000]

        # Extract links
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                from urllib.parse import urljoin
                href = urljoin(url, href)
            link_text = a.get_text(strip=True)
            if any(kw in link_text.lower() for kw in ["fellow", "grant", "fund", "scholar",
                                                        "apply", "opportunity", "program"]):
                links.append({"text": link_text, "url": href})

        return {
            "source": name,
            "url": url,
            "text": text,
            "links": links[:20],
        }
    except Exception as e:
        print(f"  Web error ({name}): {e}")
        return None


def collect_all_sources():
    """Collect from all fellowship sources."""
    rss_entries = []
    web_pages = []

    for name, url, source_type in FELLOWSHIP_SOURCES:
        print(f"  Fetching: {name}...")
        if source_type == "rss":
            entries = fetch_rss_entries(name, url)
            rss_entries.extend(entries)
            print(f"    -> {len(entries)} relevant entries")
        elif source_type == "web":
            page = fetch_web_page(name, url)
            if page:
                web_pages.append(page)
                print(f"    -> scraped ({len(page.get('links', []))} links)")

    return rss_entries, web_pages


def analyze_fellowships(rss_entries, web_pages):
    """Use GPT to find and filter relevant fellowships."""

    # Build context
    rss_text = ""
    if rss_entries:
        rss_text = "RSS ENTRIES (recent postings):\n"
        for e in rss_entries:
            rss_text += f"- [{e['source']}] {e['title']}: {e['description'][:200]}\n  Link: {e['link']}\n\n"

    web_text = "WEB PAGES (fellowship program pages):\n"
    for p in web_pages:
        links_str = "\n".join(f"  - {l['text']}: {l['url']}" for l in p.get("links", []))
        web_text += f"\n--- {p['source']} ({p['url']}) ---\n{p['text'][:3000]}\n"
        if links_str:
            web_text += f"Links found:\n{links_str}\n"

    profile_str = json.dumps(PROFILE, indent=2)

    prompt = f"""You are a fellowship and grant advisor for political science PhD students.

CANDIDATE PROFILE:
{profile_str}

IMPORTANT ELIGIBILITY NOTES:
- Candidate is a CANADIAN citizen (eligible for Canadian funding AND most US/international fellowships)
- Candidate is ABD at Harvard (eligible for dissertation fellowships)
- Focus on opportunities that match IR, security, China, IPE subfields

DATA COLLECTED FROM FELLOWSHIP SOURCES:

{rss_text}

{web_text}

TASK:
1. Identify ALL fellowships, grants, and funding opportunities from the data above that could be relevant to this candidate.
2. Also include any well-known fellowships you know of in these fields that may not appear in the scraped data (e.g., Fulbright, SSRC-DPDF, MacArthur, Jennings Randolph, etc.)
3. For each opportunity, provide: name, organization, amount (if known), deadline (if known), eligibility match (why this candidate qualifies), URL, and a brief description.
4. Sort by relevance to candidate's profile (most relevant first).
5. Flag which ones have upcoming deadlines (within the next 3 months).

Return ONLY valid JSON in this format:
[
  {{
    "name": "Fellowship Name",
    "organization": "Org Name",
    "amount": "$X,XXX or unknown",
    "deadline": "Month YYYY or unknown or rolling",
    "deadline_soon": true/false,
    "eligibility_match": "Why this candidate qualifies",
    "url": "https://...",
    "description": "Brief description",
    "relevance": "high/medium/low"
  }}
]"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are an expert academic funding advisor. Be thorough and accurate. Only return valid JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    return response.choices[0].message.content


def parse_fellowship_results(json_str):
    """Parse AI response into fellowship list."""
    s = json_str.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        if "[" in s:
            items = json.loads(s[s.find("["):s.rfind("]") + 1])
            if isinstance(items, list) and len(items) > 0:
                return True, None, items
    except json.JSONDecodeError as e:
        pass

    try:
        data = json.loads(s)
        if isinstance(data, list):
            return True, None, data
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}", None

    return False, "Could not parse fellowship results", None


def render_fellowship_email_html(fellowships):
    """Build a clean, mobile-friendly HTML email."""
    today = datetime.now().strftime("%B %d, %Y")

    # Separate by relevance
    high = [f for f in fellowships if f.get("relevance") == "high"]
    medium = [f for f in fellowships if f.get("relevance") == "medium"]
    low = [f for f in fellowships if f.get("relevance") == "low"]

    # Deadline alerts
    urgent = [f for f in fellowships if f.get("deadline_soon")]

    def render_section(items, accent):
        html = ""
        for f in items:
            deadline_badge = ""
            if f.get("deadline_soon"):
                deadline_badge = '<span style="display:inline-block;background:#dc2626;color:#fff;font-size:11px;padding:2px 6px;border-radius:3px;margin-left:8px;">DEADLINE SOON</span>'

            amount_str = f.get("amount", "unknown")
            deadline_str = f.get("deadline", "unknown")

            html += f"""
      <div style="margin-bottom:14px;padding:14px;background:#fafafa;border-radius:6px;border-left:3px solid {accent};">
        <a href="{f.get('url', '#')}" style="font-size:15px;font-weight:600;color:#1a1a2e;text-decoration:none;">{f['name']}</a>
        {deadline_badge}
        <p style="font-size:12px;color:#888;margin:4px 0;">{f.get('organization', '')} · {amount_str} · Deadline: {deadline_str}</p>
        <p style="font-size:13px;color:#444;margin:6px 0;">{f.get('description', '')}</p>
        <p style="font-size:12px;color:#2563eb;margin:4px 0;font-style:italic;">{f.get('eligibility_match', '')}</p>
      </div>"""
        return html

    urgent_section = ""
    if urgent:
        urgent_section = f"""
    <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:16px;margin-bottom:20px;">
      <h3 style="margin:0 0 8px;color:#dc2626;font-size:15px;">Upcoming Deadlines (next 3 months)</h3>
      {"".join(f'<p style="margin:4px 0;font-size:13px;">&#128680; <strong>{f["name"]}</strong> — {f.get("deadline", "TBD")} <a href="{f.get("url", "#")}" style="color:#2563eb;">Apply</a></p>' for f in urgent)}
    </div>"""

    html = f"""<html><body style="margin:0;padding:0;background:#f0f0f0;">
    <div style="max-width:600px;margin:0 auto;padding:16px;">
    <div style="background:#ffffff;border-radius:8px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#222;">

    <div style="background:#1e3a5f;padding:24px 20px;text-align:center;">
      <h1 style="margin:0;font-size:22px;font-weight:700;color:#ffffff;">Fellowship & Grant Digest</h1>
      <p style="margin:6px 0 0;font-size:13px;color:#8e8ea0;">{today} · Weekly scan for IR/Security/China opportunities</p>
    </div>

    <div style="padding:20px;">

    {urgent_section}

    <p style="font-size:13px;color:#666;margin-bottom:16px;">Found <strong>{len(fellowships)}</strong> opportunities ({len(high)} high relevance, {len(medium)} medium, {len(low)} low)</p>"""

    if high:
        html += f"""
    <h2 style="font-size:16px;color:#1e3a5f;margin:0 0 12px;padding-bottom:8px;border-bottom:2px solid #16a34a;">High Relevance</h2>
    {render_section(high, "#16a34a")}"""

    if medium:
        html += f"""
    <h2 style="font-size:16px;color:#1e3a5f;margin:20px 0 12px;padding-bottom:8px;border-bottom:2px solid #d97706;">Medium Relevance</h2>
    {render_section(medium, "#d97706")}"""

    if low:
        html += f"""
    <h2 style="font-size:16px;color:#1e3a5f;margin:20px 0 12px;padding-bottom:8px;border-bottom:2px solid #9ca3af;">Lower Relevance</h2>
    {render_section(low, "#9ca3af")}"""

    html += """
    </div>

    <div style="background:#f8f8f8;padding:16px 20px;text-align:center;border-top:1px solid #eee;">
      <p style="margin:0;font-size:11px;color:#999;">AI-curated fellowship digest · Verify deadlines and eligibility before applying</p>
    </div>

    </div></div></body></html>"""

    return html


def render_fellowship_email_text(fellowships):
    """Plain text version."""
    text = "Weekly Fellowship & Grant Digest\n" + "=" * 40 + "\n\n"

    urgent = [f for f in fellowships if f.get("deadline_soon")]
    if urgent:
        text += "UPCOMING DEADLINES:\n"
        for f in urgent:
            text += f"  ! {f['name']} — {f.get('deadline', 'TBD')} — {f.get('url', '')}\n"
        text += "\n"

    for relevance in ["high", "medium", "low"]:
        items = [f for f in fellowships if f.get("relevance") == relevance]
        if items:
            text += f"\n{relevance.upper()} RELEVANCE\n" + "-" * 30 + "\n"
            for f in items:
                text += f"\n{f['name']} ({f.get('organization', '')})\n"
                text += f"  Amount: {f.get('amount', 'unknown')}\n"
                text += f"  Deadline: {f.get('deadline', 'unknown')}\n"
                text += f"  {f.get('description', '')}\n"
                text += f"  Match: {f.get('eligibility_match', '')}\n"
                text += f"  URL: {f.get('url', '')}\n"

    text += "\n---\nAI-curated. Verify all details before applying.\n"
    return text


def send_fellowship_email(html, text):
    """Send fellowship digest to georgeyean@gmail.com."""
    today = datetime.now().strftime("%Y-%m-%d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Weekly Fellowship Digest ({today})"
    msg["From"] = f"Fellowship Finder <{GMAIL_USER}>"
    msg["To"] = "georgeyean@gmail.com"

    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)
    print("Fellowship digest sent to georgeyean@gmail.com")


def send_fellowship_failure_alert(reason, raw_response=""):
    """Send failure notification."""
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"Fellowship Finder <{GMAIL_USER}>"
        msg["To"] = "georgeyean@gmail.com"
        msg["Subject"] = f"Fellowship Digest FAILED - {datetime.now().strftime('%Y-%m-%d')}"

        html = f"""\
        <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
          <h2 style="color:#c0392b;">Fellowship Digest Failed</h2>
          <p><strong>Reason:</strong> {reason}</p>
          <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
          <pre style="background:#f5f5f5;padding:12px;border-radius:4px;font-size:12px;overflow-x:auto;white-space:pre-wrap;">{raw_response[:3000]}</pre>
        </div>"""

        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
        print("Fellowship failure alert sent")
    except Exception as e:
        print(f"Failed to send fellowship alert: {e}")


def fellowship_weekly_scan():
    """Main function: scan for fellowships and send digest."""
    print("Starting weekly fellowship scan...")

    rss_entries, web_pages = collect_all_sources()
    print(f"Collected {len(rss_entries)} RSS entries, {len(web_pages)} web pages")

    if not rss_entries and not web_pages:
        send_fellowship_failure_alert("No data collected from any source")
        return

    raw_analysis = analyze_fellowships(rss_entries, web_pages)

    valid, reason, fellowships = parse_fellowship_results(raw_analysis)
    if not valid:
        print(f"AI returned invalid content: {reason}")
        send_fellowship_failure_alert(reason, raw_analysis)
        return

    print(f"Found {len(fellowships)} opportunities")

    html = render_fellowship_email_html(fellowships)
    text = render_fellowship_email_text(fellowships)

    send_fellowship_email(html, text)
    print("Fellowship scan done.")


if __name__ == "__main__":
    fellowship_weekly_scan()
