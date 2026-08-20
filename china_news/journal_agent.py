import requests
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import quote
from openai import OpenAI
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False
    print("Warning: pypdf not installed. PDF text extraction disabled. Run: pip install pypdf")

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    print("Warning: beautifulsoup4 not installed. Publisher page scraping disabled. Run: pip install beautifulsoup4")

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GMAIL_USER = os.getenv("EMAIL_FROM", "georgeyean@gmail.com")
GMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = "georgeyean@gmail.com"

client = OpenAI(api_key=OPENAI_API_KEY)

TEST_MODE = True  # True → send only to EMAIL_TO; False → send to all academic subscribers

UNPAYWALL_EMAIL = "georgeyean@gmail.com"
PAPERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "papers")
PAPERS_BASE_URL = "https://agapionline.us/papers"
PAPERS_PER_JOURNAL = 5  # how many recent papers to fetch per journal each run

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# ── Journal registry ──────────────────────────────────────────────────────────
# To add a journal: append an entry with its print ISSN and display name.
# The seen-DOI file is created automatically per journal.

JOURNALS = [
    {
        "name": "American Political Science Review",
        "short": "APSR",
        "issn": "0003-0554",
        "seen_file": os.path.join(PAPERS_DIR, "apsr_seen.txt"),
        "accent": "#1e3a5f",
    },
    {
        "name": "American Journal of Political Science",
        "short": "AJPS",
        "issn": "0092-5853",
        "seen_file": os.path.join(PAPERS_DIR, "ajps_seen.txt"),
        "accent": "#7c3aed",
    },
    {
        "name": "Journal of Politics",
        "short": "JOP",
        "issn": "0022-3816",
        "seen_file": os.path.join(PAPERS_DIR, "jop_seen.txt"),
        "accent": "#b45309",
    },
    # More journals to consider:
    # {"name": "International Organization", "short": "IO",  "issn": "0020-8183", "seen_file": os.path.join(PAPERS_DIR, "io_seen.txt"),  "accent": "#0f766e"},
    # {"name": "World Politics",             "short": "WP",  "issn": "0043-8871", "seen_file": os.path.join(PAPERS_DIR, "wp_seen.txt"),  "accent": "#be123c"},
    # {"name": "Journal of Conflict Resolution", "short": "JCR", "issn": "0022-0027", "seen_file": os.path.join(PAPERS_DIR, "jcr_seen.txt"), "accent": "#92400e"},
]


# ── Seen-DOI tracking ─────────────────────────────────────────────────────────

def load_seen_dois(seen_file):
    os.makedirs(os.path.dirname(seen_file), exist_ok=True)
    if not os.path.exists(seen_file):
        return set()
    with open(seen_file, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def mark_doi_seen(seen_file, doi):
    with open(seen_file, "a", encoding="utf-8") as f:
        f.write(doi.strip() + "\n")


# ── Paper fetching ─────────────────────────────────────────────────────────────

NON_PAPER_PATTERNS = re.compile(
    r"^(cover|front matter|back matter|table of contents|editorial board|"
    r"book review|volume \d+|issue \d+|in memoriam|acknowledgment)",
    re.IGNORECASE,
)

CORRIGENDUM_PATTERNS = re.compile(
    r"(corrigendum|erratum|– correction|– reply to|– response to)",
    re.IGNORECASE,
)

def is_real_paper(title, authors):
    """Return False for covers, front matter, and other non-article items."""
    if not title:
        return False
    if NON_PAPER_PATTERNS.match(title.strip()):
        return False
    if not authors:
        return False
    return True

def is_corrigendum(title):
    return bool(CORRIGENDUM_PATTERNS.search((title or "").strip()))


def fetch_latest_papers(issn, max_results=25):
    """Pull recent articles from CrossRef for a given ISSN."""
    url = f"https://api.crossref.org/journals/{issn}/works"
    params = {
        "sort": "published",
        "order": "desc",
        "rows": max_results,
        "filter": "type:journal-article",
    }
    items = None
    for attempt in range(1, 6):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            items = resp.json().get("message", {}).get("items", [])
            break
        except Exception as e:
            if attempt < 5:
                print(f"  CrossRef error (ISSN {issn}), attempt {attempt}/5: {e} — retrying in 2 min...")
                time.sleep(120)
            else:
                print(f"  CrossRef unavailable (ISSN {issn}) after 5 attempts: {e}")
    if items is None:
        return None  # None = server unavailable; [] = success but no items

    papers = []
    for item in items:
        doi = item.get("DOI", "").strip()
        if not doi:
            continue

        title_list = item.get("title", [])
        title = title_list[0].strip() if title_list else ""

        authors = []
        for a in item.get("author", []):
            name = f"{a.get('given', '')} {a.get('family', '')}".strip()
            if name:
                authors.append(name)

        if not is_real_paper(title, authors):
            print(f"    Skipping non-paper: {title[:60]}")
            continue

        raw_abstract = item.get("abstract", "")
        abstract = re.sub(r"<[^>]+>", "", raw_abstract).strip()

        pub_date = item.get("published-print") or item.get("published-online") or {}
        date_parts = (pub_date.get("date-parts") or [[]])[0]
        year = date_parts[0] if date_parts else None

        papers.append({
            "doi": doi,
            "title": title,
            "authors": authors,
            "is_corrigendum": is_corrigendum(title),
            "abstract": abstract,
            "year": year,
            "url": item.get("URL", f"https://doi.org/{doi}"),
        })

    return papers


# ── Free-version search ───────────────────────────────────────────────────────

def find_free_version(doi, title):
    """Try Unpaywall then Semantic Scholar; return (url, source_label) or (None, None)."""

    # 1. Unpaywall
    try:
        resp = requests.get(
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": UNPAYWALL_EMAIL},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("is_oa"):
                best = data.get("best_oa_location") or {}
                url = best.get("url_for_pdf") or best.get("url")
                if url:
                    return url, "Unpaywall (Open Access)"
    except Exception:
        pass

    # 2. Semantic Scholar — by DOI
    try:
        resp = requests.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params={"fields": "openAccessPdf"},
            timeout=10,
        )
        if resp.status_code == 200:
            pdf_info = resp.json().get("openAccessPdf") or {}
            if pdf_info.get("url"):
                return pdf_info["url"], "Semantic Scholar (Open Access PDF)"
    except Exception:
        pass

    # 3. Semantic Scholar — by title search
    try:
        resp = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": title, "fields": "openAccessPdf", "limit": 3},
            timeout=10,
        )
        if resp.status_code == 200:
            for paper in resp.json().get("data", []):
                pdf_info = paper.get("openAccessPdf") or {}
                if pdf_info.get("url"):
                    return pdf_info["url"], "Semantic Scholar (title search)"
    except Exception:
        pass

    # 4. Google Scholar — finds author-hosted PDFs on institutional pages
    for url, source in _google_scholar_pdf_urls(title):
        return url, f"Google Scholar ({source})"

    return None, None


ABSTRACT_SELECTORS = [
    # Cambridge Core (APSR, JOP, AJPS)
    {"name": "div", "attrs": {"class": re.compile(r"abstract")}},
    # Oxford Academic
    {"name": "section", "attrs": {"class": re.compile(r"abstract")}},
    # Wiley
    {"name": "div", "attrs": {"class": "article-section__content"}},
    # Generic
    {"name": "div", "attrs": {"id": "abstract"}},
    {"name": "p",   "attrs": {"class": re.compile(r"abstract")}},
]

def scrape_abstract_from_publisher(doi):
    """Follow the DOI URL to the publisher page and scrape the abstract."""
    if not HAS_BS4:
        return ""
    try:
        resp = requests.get(
            f"https://doi.org/{doi}",
            headers=HEADERS,
            timeout=15,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return ""

        soup = BeautifulSoup(resp.text, "html.parser")

        for selector in ABSTRACT_SELECTORS:
            el = soup.find(selector["name"], selector["attrs"])
            if el:
                text = el.get_text(separator=" ", strip=True)
                # Strip leading "Abstract" label if present
                text = re.sub(r"^abstract[:\s]*", "", text, flags=re.IGNORECASE).strip()
                if len(text) > 80:
                    return text

        return ""
    except Exception as e:
        print(f"      Publisher scrape failed: {e}")
        return ""


def enrich_abstract(doi, title, existing_abstract, authors=None):
    """Try multiple sources to get the abstract, in order of reliability."""
    if existing_abstract and len(existing_abstract) > 80:
        return existing_abstract

    def _title_matches(candidate_title, expected_title, threshold=0.6):
        """Rough word-overlap check to avoid returning a wrong paper's abstract."""
        a = set(re.sub(r"[^\w\s]", "", (candidate_title or "").lower()).split())
        b = set(re.sub(r"[^\w\s]", "", (expected_title or "").lower()).split())
        if not a or not b:
            return False
        overlap = len(a & b) / max(len(a), len(b))
        return overlap >= threshold

    def _invert_abstract(inv):
        words = {}
        for word, positions in inv.items():
            for pos in positions:
                words[pos] = word
        return " ".join(words[i] for i in sorted(words))

    # 1. Semantic Scholar by DOI — exact match, always trust
    try:
        resp = requests.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params={"fields": "abstract"},
            timeout=10,
        )
        if resp.status_code == 200:
            abstract = resp.json().get("abstract", "")
            if abstract and len(abstract) > 80:
                return abstract.strip()
    except Exception:
        pass

    # 2. OpenAlex by DOI — exact match, always trust
    try:
        resp = requests.get(
            f"https://api.openalex.org/works/https://doi.org/{doi}",
            params={"select": "abstract_inverted_index"},
            timeout=10,
        )
        if resp.status_code == 200:
            inv = resp.json().get("abstract_inverted_index") or {}
            if inv:
                abstract = _invert_abstract(inv)
                if len(abstract) > 80:
                    return abstract.strip()
    except Exception:
        pass

    # 3. Scrape publisher page via DOI redirect (works for non-JS sites)
    print(f"      Scraping publisher page for abstract...")
    scraped = scrape_abstract_from_publisher(doi)
    if scraped:
        return scraped

    # 4. Semantic Scholar title search — verify title matches before using
    try:
        resp = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": title, "fields": "title,abstract", "limit": 5},
            timeout=10,
        )
        if resp.status_code == 200:
            for paper in resp.json().get("data", []):
                if not _title_matches(paper.get("title", ""), title):
                    continue
                abstract = paper.get("abstract", "")
                if abstract and len(abstract) > 80:
                    return abstract.strip()
    except Exception:
        pass

    # 5. OpenAlex title search — verify title matches before using
    try:
        resp = requests.get(
            "https://api.openalex.org/works",
            params={"search": title, "per-page": 5, "select": "title,abstract_inverted_index"},
            timeout=10,
        )
        if resp.status_code == 200:
            for work in resp.json().get("results", []):
                if not _title_matches(work.get("title", ""), title):
                    continue
                inv = work.get("abstract_inverted_index") or {}
                if inv:
                    abstract = _invert_abstract(inv)
                    if len(abstract) > 80:
                        return abstract.strip()
    except Exception:
        pass

    # 6. Google Scholar snippet — search by title, grab the result snippet
    if HAS_BS4:
        try:
            resp = requests.get(
                "https://scholar.google.com/scholar",
                params={"q": title},
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for result in soup.select("div.gs_ri"):
                    result_title_el = result.select_one("h3.gs_rt")
                    result_title_text = result_title_el.get_text(" ", strip=True) if result_title_el else ""
                    if not _title_matches(result_title_text, title):
                        continue
                    snippet = result.select_one("div.gs_rs")
                    if snippet:
                        snip_text = snippet.get_text(" ", strip=True)
                        if len(snip_text) > 80:
                            print(f"      Abstract from Google Scholar snippet")
                            return snip_text
        except Exception:
            pass

    # 7. arXiv full-text search
    try:
        resp = requests.get(
            "https://export.arxiv.org/search/",
            params={"query": title, "searchtype": "all", "max_results": 3},
            timeout=10,
        )
        if resp.status_code == 200 and HAS_BS4:
            soup = BeautifulSoup(resp.text, "html.parser")
            for entry in soup.select("li.arxiv-result"):
                entry_title = entry.select_one("p.title")
                entry_title_text = entry_title.get_text(" ", strip=True) if entry_title else ""
                if not _title_matches(entry_title_text, title):
                    continue
                abstract_el = entry.select_one("span.abstract-full, p.abstract")
                if abstract_el:
                    text = abstract_el.get_text(" ", strip=True)
                    text = re.sub(r"^\s*abstract[:\s]*", "", text, flags=re.IGNORECASE).strip()
                    if len(text) > 80:
                        print(f"      Abstract from arXiv")
                        return text
    except Exception:
        pass

    # 8. SSRN title search
    if HAS_BS4:
        try:
            resp = requests.get(
                "https://papers.ssrn.com/sol3/results.cfm",
                params={"RequestTimeout": "50000", "txtSearchQuery": title, "Search": "Search"},
                headers={"User-Agent": HEADERS["User-Agent"]},
                timeout=15,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                for item in soup.select("div.title-section"):
                    item_title = item.select_one("a.title")
                    item_title_text = item_title.get_text(" ", strip=True) if item_title else ""
                    if not _title_matches(item_title_text, title):
                        continue
                    # get abstract from detail page
                    href = item_title.get("href", "") if item_title else ""
                    if href:
                        detail = requests.get(href, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=10)
                        if detail.status_code == 200:
                            dsoup = BeautifulSoup(detail.text, "html.parser")
                            abst = dsoup.select_one("div.abstract-text p, div[id*='abstract'] p")
                            if abst:
                                text = abst.get_text(" ", strip=True)
                                if len(text) > 80:
                                    print(f"      Abstract from SSRN")
                                    return text
        except Exception:
            pass

    # 9. DuckDuckGo web search — author + title, find PDFs or pages with abstract
    if HAS_BS4:
        try:
            from urllib.parse import urlparse as _up, parse_qs as _pqs, unquote as _uq
            first_author = (authors[0].split()[-1] if authors else "") if authors else ""
            query = f'{first_author} {title} filetype:pdf OR site:edu OR site:ssrn.com'
            resp = requests.get(
                "https://duckduckgo.com/html/",
                params={"q": query},
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")

                def _ddg_url(href):
                    if "duckduckgo.com/l/" in href:
                        qs = _pqs(_up(href).query)
                        return _uq(qs.get("uddg", [""])[0])
                    return href if href.startswith("http") else ""

                urls = [_ddg_url(a.get("href", "")) for a in soup.select(".result__a")]
                urls = [u for u in urls if u.startswith("http") and "duckduckgo" not in u]

                skip = {"scholar.google", "google.com/search", "twitter.com", "reddit.com", "wikipedia.org"}
                title_words = set(re.sub(r"[^\w]", " ", title.lower()).split()) - {"and", "the", "of", "in", "a"}

                for url in urls[:8]:
                    if any(s in url for s in skip):
                        continue
                    try:
                        page = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=10)
                        if page.status_code != 200:
                            continue
                        ctype = page.headers.get("content-type", "")
                        # PDF: extract text and look for abstract section
                        if url.lower().endswith(".pdf") or "application/pdf" in ctype:
                            if page.content[:4] == b"%PDF" and HAS_PYPDF:
                                import io
                                reader = pypdf.PdfReader(io.BytesIO(page.content))
                                pdf_text = ""
                                for pg in reader.pages[:3]:
                                    t = pg.extract_text()
                                    if t:
                                        pdf_text += t
                                pdf_sample_words = set(re.sub(r"[^\w]", " ", pdf_text[:3000].lower()).split())
                                title_ok = title_words and len(title_words & pdf_sample_words) / len(title_words) >= 0.5
                                author_ok = not first_author or first_author.lower() in pdf_text[:3000].lower()
                                if pdf_text and title_ok and author_ok:
                                    abst_m = re.search(r"abstract[:\s]+(.*?)(?:introduction|keywords|\n1\.|\ni\.)", pdf_text[:4000], re.IGNORECASE | re.DOTALL)
                                    if abst_m:
                                        abst = abst_m.group(1).strip()[:2000]
                                        if len(abst) > 80:
                                            print(f"      Abstract from author PDF: {url[:60]}")
                                            return abst
                            continue
                        # HTML: look for abstract block
                        psoup = BeautifulSoup(page.text, "html.parser")
                        for sel in ["div.abstract", "section.abstract", "#abstract", "p.abstract",
                                    "div[class*='abstract']", "div[id*='abstract']"]:
                            el = psoup.select_one(sel)
                            if el:
                                text = re.sub(r"^\s*abstract[:\s]*", "", el.get_text(" ", strip=True), flags=re.IGNORECASE).strip()
                                if len(text) > 80:
                                    print(f"      Abstract from web: {url[:60]}")
                                    return text
                        # fallback: long paragraph with title word overlap
                        for p_el in psoup.select("p"):
                            ptext = p_el.get_text(" ", strip=True)
                            if len(ptext) > 150 and title_words:
                                pwords = set(re.sub(r"[^\w]", " ", ptext.lower()).split())
                                if len(title_words & pwords) / len(title_words) > 0.5:
                                    print(f"      Abstract (paragraph) from: {url[:60]}")
                                    return ptext[:2000]
                    except Exception:
                        continue
        except Exception:
            pass

    return existing_abstract or ""


# ── PDF download ──────────────────────────────────────────────────────────────

def make_filename(authors, year, title):
    """Build a readable filename: FirstAuthorLastname_Year_First_Five_Title_Words.pdf"""
    # First author last name
    if authors:
        last_name = authors[0].split()[-1]
    else:
        last_name = "Unknown"
    last_name = re.sub(r"[^\w]", "", last_name)

    year_str = str(year) if year else "0000"

    # First 6 words of title, title-cased, underscored
    words = re.sub(r"[^\w\s]", "", title).split()[:6]
    title_slug = "_".join(w.capitalize() for w in words)

    return f"{last_name}_{year_str}_{title_slug}.pdf"


def paper_web_url(journal_short, filename):
    """Return a web-accessible URL for a saved PDF, if PAPERS_BASE_URL is configured."""
    if not PAPERS_BASE_URL:
        return None
    return f"{PAPERS_BASE_URL.rstrip('/')}/{journal_short}/{filename}"


def _try_save_pdf(resp, local_path):
    """Write response bytes to disk if content looks like a PDF. Returns True on success."""
    content_type = resp.headers.get("Content-Type", "")
    if "pdf" not in content_type and "octet-stream" not in content_type:
        return False
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    # Sanity-check: real PDFs start with %PDF
    with open(local_path, "rb") as f:
        magic = f.read(4)
    if magic != b"%PDF":
        os.remove(local_path)
        return False
    size_kb = os.path.getsize(local_path) // 1024
    print(f"      Saved PDF ({size_kb} KB): {local_path}")
    return True


def _extract_pdf_link_from_html(html_text, base_url):
    """Parse an HTML landing page for a direct PDF href."""
    if not HAS_BS4:
        return None
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" in href.lower() or "pdf" in a.get("class", [""]):
            if not href.startswith("http"):
                from urllib.parse import urljoin
                href = urljoin(base_url, href)
            return href
    # Also check meta refresh / canonical PDF viewer links
    for link in soup.find_all("link", {"type": "application/pdf"}):
        return link.get("href")
    return None


def _google_scholar_pdf_urls(title):
    """Scrape Google Scholar search results for [PDF] links."""
    if not HAS_BS4:
        return
    try:
        resp = requests.get(
            "https://scholar.google.com/scholar",
            params={"q": title},
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return
        soup = BeautifulSoup(resp.text, "html.parser")
        # Google Scholar marks PDF links with class "gs_or_ggsm" or span "[PDF]"
        for tag in soup.select("div.gs_or_ggsm a, a.gs_or_ggsm"):
            href = tag.get("href", "")
            if href.startswith("http"):
                yield href, "Google Scholar"
        # Fallback: any link containing .pdf in the results
        for a in soup.select("div.gs_r a"):
            href = a.get("href", "")
            if ".pdf" in href.lower() and href.startswith("http"):
                yield href, "Google Scholar"
    except Exception:
        pass


def _alternate_pdf_urls(doi, title):
    """
    Yield candidate free PDF URLs from alternate sources to try when
    the primary URL fails.
    """
    # 1. Google Scholar — finds author-hosted PDFs (institutional pages, etc.)
    yield from _google_scholar_pdf_urls(title)

    # 2. arXiv title search
    try:
        resp = requests.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"ti:{title}", "max_results": 3},
            timeout=10,
        )
        if resp.status_code == 200:
            for arxiv_id in re.findall(r"arxiv\.org/abs/([\d.]+)", resp.text):
                yield f"https://arxiv.org/pdf/{arxiv_id}.pdf", f"arXiv:{arxiv_id}"
    except Exception:
        pass

    # 3. Semantic Scholar open-access PDF
    try:
        resp = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": title, "fields": "openAccessPdf,externalIds", "limit": 5},
            timeout=10,
        )
        if resp.status_code == 200:
            for paper in resp.json().get("data", []):
                pdf_info = paper.get("openAccessPdf") or {}
                if pdf_info.get("url"):
                    yield pdf_info["url"], "Semantic Scholar"
    except Exception:
        pass

    # 3. OSF (Open Science Framework)
    try:
        resp = requests.get(
            "https://api.osf.io/v2/nodes/",
            params={"filter[title]": title, "page[size]": 3},
            timeout=10,
        )
        if resp.status_code == 200:
            for node in resp.json().get("data", []):
                node_id = node.get("id", "")
                if node_id:
                    yield f"https://osf.io/{node_id}/download", "OSF"
    except Exception:
        pass


def download_paper(pdf_url, journal_short, authors, year, title, doi=""):
    """
    Download a legally free PDF and save to papers/<journal>/Author_Year_Title.pdf.
    If the primary URL returns HTML, parse it for a PDF link.
    If that fails, try alternate sources (arXiv, Semantic Scholar, OSF).
    Returns the local path on success, None on failure.
    """
    try:
        save_dir = os.path.join(PAPERS_DIR, journal_short)
        os.makedirs(save_dir, exist_ok=True)

        filename = make_filename(authors, year, title)
        local_path = os.path.join(save_dir, filename)

        if os.path.exists(local_path):
            return local_path  # already downloaded

        # ── Attempt 1: primary URL ────────────────────────────────────────────
        resp = requests.get(pdf_url, headers=HEADERS, timeout=30, stream=True)
        resp.raise_for_status()

        if _try_save_pdf(resp, local_path):
            return local_path

        # Got HTML — try to find a PDF link inside it
        print(f"      Primary URL returned HTML, scanning for PDF link...")
        html_text = resp.text if hasattr(resp, "text") else ""
        embedded_pdf = _extract_pdf_link_from_html(html_text, pdf_url)
        if embedded_pdf:
            try:
                resp2 = requests.get(embedded_pdf, headers=HEADERS, timeout=30, stream=True)
                resp2.raise_for_status()
                if _try_save_pdf(resp2, local_path):
                    print(f"      Saved via embedded PDF link")
                    return local_path
            except Exception:
                pass

        # ── Attempt 2: alternate sources ──────────────────────────────────────
        print(f"      Trying alternate sources...")
        for alt_url, alt_source in _alternate_pdf_urls(doi, title):
            try:
                resp3 = requests.get(alt_url, headers=HEADERS, timeout=20, stream=True)
                resp3.raise_for_status()
                if _try_save_pdf(resp3, local_path):
                    print(f"      Saved via {alt_source}")
                    return local_path
            except Exception:
                continue

        print(f"      Could not download PDF from any source")
        return None

    except Exception as e:
        print(f"    PDF download failed: {e}")
        return None


def extract_pdf_text(local_path, max_chars=12000):
    """Extract plain text from a local PDF. Returns empty string on failure."""
    if not HAS_PYPDF or not local_path or not os.path.exists(local_path):
        return ""
    try:
        reader = pypdf.PdfReader(local_path)
        pages_text = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages_text.append(t)
        full = "\n".join(pages_text)
        return full[:max_chars]
    except Exception as e:
        print(f"    PDF text extraction failed: {e}")
        return ""


# ── GPT analysis ──────────────────────────────────────────────────────────────

def analyze_corrigendum(paper):
    """Lightweight GPT call: just extract what was corrected."""
    content = paper.get("full_text") or paper.get("abstract") or "Not available"
    prompt = f"""This is a corrigendum/erratum notice:

Title: {paper['title']}
Content: {content}

In one sentence, state what was corrected (which paper, which error). If content is not available, write "Not available"."""
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "Not available"


SUBFIELD_OPTIONS = "CP (Comparative Politics) / IR (International Relations) / AP (American Politics) / Theory / Method / Other"

_NO_CONTENT = {
    "content_missing": True,
    "subfield": "",
    "main_argument": "",
    "method_data": "",
    "why_published": "",
}


def analyze_paper(paper, journal_name):
    authors_str = ", ".join(paper["authors"][:5]) or "Unknown"

    has_full_text = bool(paper.get("full_text", "").strip())
    has_abstract = bool((paper.get("abstract") or "").strip())

    if not has_full_text and not has_abstract:
        print(f"    Skipping GPT — no content for '{paper['title'][:60]}'")
        return dict(_NO_CONTENT)

    content = paper["full_text"] if has_full_text else paper["abstract"]
    source_label = "FULL PAPER TEXT" if has_full_text else "ABSTRACT"

    content_block = (
        f"Title: {paper['title']}\n"
        f"Authors: {authors_str}\n"
        f"Year: {paper.get('year') or 'unknown'}\n\n"
        f"--- {source_label} ---\n"
        f"{content}"
    )

    prompt = f"""You are an expert political scientist. Read the following content from a paper published in {journal_name} and answer precisely.

{content_block}

Based on the text above, provide:
1. subfield — one or two tags from: CP, IR, AP, Theory, Method, Other. Use two (e.g. "CP/IR") when the paper genuinely engages both fields.
2. main_argument — 1-2 sentences: the paper's central thesis or causal claim. Do NOT fabricate specific findings or numbers.
3. method_data — name the SPECIFIC data source exactly as it appears in the text (e.g. "Difference-in-differences using ANES 2020 survey", "Survey experiment on German Twitter users (2018–2022)", "Factiva newspaper archives 1995–2010"). Never say "various sources" or "existing datasets". Write "Not available" if it cannot be determined from the text.
4. why_published — exactly ONE sentence: the single strongest reason this merits publication in {journal_name}.

Return ONLY valid JSON (no markdown fences):
{{
  "subfield": "...",
  "main_argument": "...",
  "method_data": "...",
  "why_published": "..."
}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=600,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as e:
        print(f"    GPT error for '{paper['title'][:60]}': {e}")
        return {
            "subfield": "?",
            "main_argument": "Analysis failed",
            "method_data": "Analysis failed",
            "why_published": "Analysis failed",
        }


# ── Email rendering ───────────────────────────────────────────────────────────

def render_journal_section_html(journal, papers):
    accent = journal["accent"]
    section_html = f"""
<div style="margin-bottom:32px;">
  <div style="padding:10px 14px;background:{accent};border-radius:6px 6px 0 0;">
    <h2 style="margin:0;font-size:16px;font-weight:700;color:#fff;">{journal['name']} ({journal['short']})</h2>
    <p style="margin:4px 0 0;font-size:12px;color:rgba(255,255,255,0.75);">{len(papers)} new paper{'s' if len(papers) != 1 else ''}</p>
  </div>"""

    for p in papers:
        a = p.get("analysis", {})
        authors_str = ", ".join(p["authors"][:3])
        if len(p["authors"]) > 3:
            authors_str += " et al."

        links = []
        if p.get("free_url"):
            links.append(f'<a href="{p["free_url"]}" style="font-size:12px;color:#16a34a;text-decoration:none;">Open Access</a>')
        if p.get("local_path"):
            # web_url requires PAPERS_BASE_URL to be set; fall back to free_url until then
            backup_href = p.get("web_url") or p.get("free_url")
            if backup_href:
                links.append(f'<a href="{backup_href}" style="font-size:12px;color:#2563eb;text-decoration:none;">Backup copy</a>')
        if not links:
            label = "Click to search manually" if a.get("content_missing") else "Search for paper"
            links.append(
                f'<a href="https://scholar.google.com/scholar?q={quote(p["title"])}" '
                f'style="font-size:12px;color:#d97706;text-decoration:none;">{label}</a>'
            )
        free_link_html = ' &nbsp;·&nbsp; '.join(links)
        free_badge = ""

        subfield = a.get("subfield", "")
        subfield_badge = (
            f'<span style="display:inline-block;background:#374151;color:#fff;'
            f'font-size:11px;font-weight:700;padding:2px 7px;border-radius:3px;'
            f'margin-right:6px;letter-spacing:0.3px;">{subfield}</span>'
            if subfield else ""
        )

        verbatim_abstract = p.get("abstract", "").strip()
        abstract_html = (
            f'<p class="abst" style="font-size:11px;color:#666;line-height:1.5;margin:8px 0 10px;'
            f'font-style:italic;border-left:2px solid #ddd;padding-left:8px;">'
            f'{verbatim_abstract}</p>'
            if verbatim_abstract
            else '<p class="abst" style="font-size:11px;color:#aaa;margin:6px 0 10px;">Abstract not available</p>'
        )

        if p.get("is_corrigendum"):
            correction_note = p.get("correction_note", "Not available")
            section_html += f"""
  <div style="margin:0;padding:10px 14px;background:#fafafa;border-left:3px solid #9ca3af;border-bottom:1px solid #eee;">
    <a href="{p['url']}" style="font-size:14px;font-weight:600;color:#6b7280;text-decoration:none;">{p['title']}</a>
    <p style="font-size:12px;color:#aaa;margin:2px 0 6px;">{authors_str} · {journal['short']} {p.get('year') or ''}</p>
    <p style="font-size:12px;color:#555;margin:0 0 6px;"><strong>Corrects:</strong> {correction_note}</p>
    <div style="margin-top:4px;">{free_link_html}</div>
  </div>"""
        else:
            if a.get("content_missing"):
                analysis_html = '<p style="font-size:12px;color:#b45309;margin:6px 0;font-style:italic;">⚠ Paper not accessible — analysis unavailable.</p>'
            else:
                analysis_html = (
                    '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
                    "<tr>"
                    '<td style="padding:5px 8px;vertical-align:top;width:28%;background:#eef2f7;font-weight:600;color:#1e3a5f;">Main Argument</td>'
                    f'<td style="padding:5px 8px;vertical-align:top;color:#444;">{a.get("main_argument","")}</td>'
                    "</tr><tr>"
                    '<td style="padding:5px 8px;vertical-align:top;background:#eef2f7;font-weight:600;color:#1e3a5f;">Method &amp; Data</td>'
                    f'<td style="padding:5px 8px;vertical-align:top;color:#444;">{a.get("method_data","")}</td>'
                    "</tr><tr>"
                    '<td style="padding:5px 8px;vertical-align:top;background:#eef2f7;border-radius:0 0 0 4px;font-weight:600;color:#1e3a5f;">Why Published?</td>'
                    f'<td style="padding:5px 8px;vertical-align:top;color:#555;font-style:italic;">{a.get("why_published","")}</td>'
                    "</tr></table>"
                )
            section_html += f"""
  <div style="margin:0;padding:14px;background:#fafafa;border-left:3px solid {accent};border-bottom:1px solid #eee;">
    <div style="margin-bottom:6px;">{subfield_badge}<a href="{p['url']}" style="font-size:15px;font-weight:600;color:#1a1a2e;text-decoration:none;line-height:1.3;">{p['title']}</a></div>
    <p style="font-size:12px;color:#888;margin:0 0 4px;">{authors_str} · {journal['short']} {p.get('year') or ''}</p>
    {abstract_html}
    {analysis_html}
    <div style="margin-top:8px;">{free_link_html}</div>
  </div>"""

    section_html += "</div>"
    return section_html


def render_email_html(results_by_journal):
    today = datetime.now().strftime("%B %d, %Y")
    total = sum(len(p) for p in results_by_journal.values() if p is not None)

    body = ""
    for journal in JOURNALS:
        papers = results_by_journal.get(journal["short"])
        if papers is None and journal["short"] in results_by_journal:
            # server was unavailable for this journal
            accent = journal["accent"]
            body += f"""
<div style="margin-bottom:32px;">
  <div style="padding:10px 14px;border-bottom:3px solid {accent};margin-bottom:8px;">
    <span style="font-size:13px;font-weight:700;color:{accent};text-transform:uppercase;letter-spacing:0.5px;">{journal['name']}</span>
  </div>
  <div style="padding:12px 14px;background:#fef9ec;border-left:3px solid #f59e0b;font-size:13px;color:#92400e;font-style:italic;">
    ⚠ This journal's data could not be retrieved this cycle (CrossRef temporarily unavailable). Please check back next week.
  </div>
</div>"""
        elif papers:
            body += render_journal_section_html(journal, papers)

    html = f"""<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
@media(max-width:600px){{
  .outer{{padding:4px!important}}
  .inner{{padding:8px!important}}
  .card{{padding:10px 8px!important}}
  .hdr{{padding:16px 10px!important}}
  .ftr{{padding:12px 8px!important}}
  td{{padding:4px 6px!important}}
  .abst{{font-size:13px!important}}
}}
</style></head><body style="margin:0;padding:0;background:#f0f0f0;">
<div class="outer" style="max-width:660px;margin:0 auto;padding:16px;">
<div style="background:#fff;border-radius:8px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#222;">

<div class="hdr" style="background:#111827;padding:24px 20px;text-align:center;">
  <h1 style="margin:0;font-size:22px;font-weight:700;color:#fff;letter-spacing:0.5px;">PoliSci Journal Digest</h1>
  <p style="margin:6px 0 0;font-size:13px;color:#8e8ea0;">{today} · {total} new paper{'s' if total != 1 else ''} across {len(results_by_journal)} journal{'s' if len(results_by_journal) != 1 else ''}</p>
</div>

<div class="inner" style="padding:20px;">
{body}
</div>

<div class="ftr" style="background:#f8f8f8;padding:16px 20px;text-align:center;border-top:1px solid #eee;">
  <p style="margin:0;font-size:11px;color:#999;">PoliSci Journal Digest · AI-powered paper summaries · Powered by GPT-4.1</p>
</div>

</div></div></body></html>"""
    return html


def render_email_text(results_by_journal):
    today = datetime.now().strftime("%Y-%m-%d")
    total = sum(len(p) for p in results_by_journal.values() if p is not None)
    text = f"PoliSci Journal Digest — {today}\n{'='*50}\n{total} new paper(s)\n\n"

    for journal in JOURNALS:
        papers = results_by_journal.get(journal["short"])
        if papers is None and journal["short"] in results_by_journal:
            text += f"\n{journal['name']} ({journal['short']})\n{'-'*50}\n"
            text += "⚠ Journal data unavailable this cycle (CrossRef temporarily unreachable).\n"
            continue
        if not papers:
            continue
        text += f"\n{journal['name']} ({journal['short']})\n{'-'*50}\n"
        for p in papers:
            a = p.get("analysis", {})
            authors_str = ", ".join(p["authors"][:3])
            if len(p["authors"]) > 3:
                authors_str += " et al."
            text += f"\n{p['title']}\n{authors_str} · {journal['short']} {p.get('year') or ''}\n"
            text += f"DOI: https://doi.org/{p['doi']}\n"
            if p.get("free_url"):
                text += f"Free PDF:     {p['free_url']} ({p.get('free_source','')})\n"
            if p.get("local_path"):
                text += f"Saved local: {p['local_path']}\n"
            if p.get("is_corrigendum"):
                text += f"Corrects: {p.get('correction_note', 'Not available')}\n"
            elif a.get("content_missing"):
                text += f"Abstract: {p.get('abstract','Not available')}\n"
                text += "⚠ Paper not accessible — analysis unavailable.\n"
            else:
                text += f"[{a.get('subfield','?')}] Abstract: {p.get('abstract','Not available')}\n"
                text += f"Main Argument: {a.get('main_argument','')}\n"
                text += f"Method & Data: {a.get('method_data','')}\n"
                text += f"Why Published: {a.get('why_published','')}\n"
            text += "-" * 40 + "\n"

    return text


# ── Email sending ─────────────────────────────────────────────────────────────

def _get_subscribers(list_name="academic"):
    if TEST_MODE:
        return [EMAIL_TO]
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscribers", f"{list_name}.txt")
    if not os.path.exists(filepath):
        return [EMAIL_TO]
    with open(filepath, "r", encoding="utf-8") as f:
        emails = [line.strip().lower() for line in f if line.strip()]
    return emails or [EMAIL_TO]


def _smtp_connection():
    """Try port 465 (SSL) first, fall back to port 587 (STARTTLS)."""
    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20)
        server.login(GMAIL_USER, GMAIL_PASS)
        return server
    except Exception as e:
        print(f"  Port 465 failed ({e}), trying port 587...")
    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=20)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(GMAIL_USER, GMAIL_PASS)
    return server


def send_digest_email(html, text, total_count):
    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"PoliSci Journal Digest ({today}) — {total_count} new paper{'s' if total_count != 1 else ''}"
    subscribers = _get_subscribers("academic")
    with _smtp_connection() as server:
        for recipient in subscribers:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"PoliSci Journal Digest <{GMAIL_USER}>"
            msg["To"] = recipient
            msg.attach(MIMEText(text, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))
            try:
                server.send_message(msg)
                print(f"Digest sent to {recipient}")
            except Exception as e:
                print(f"Failed to send to {recipient}: {e}")
            time.sleep(2)


def send_failure_alert(reason):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"PoliSci Journal Digest <{GMAIL_USER}>"
        msg["To"] = EMAIL_TO
        msg["Subject"] = f"PoliSci Journal Digest FAILED — {datetime.now().strftime('%Y-%m-%d')}"
        html = (
            f'<div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px;">'
            f'<h2 style="color:#c0392b;">PoliSci Journal Digest Failed</h2>'
            f'<p><strong>Reason:</strong> {reason}</p>'
            f'<p><strong>Time:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>'
            f'</div>'
        )
        msg.attach(MIMEText(html, "html", "utf-8"))
        with _smtp_connection() as server:
            server.send_message(msg)
        print("Failure alert sent")
    except Exception as e:
        print(f"Failed to send failure alert: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run=False, max_per_journal=PAPERS_PER_JOURNAL):
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Starting weekly journal scan..."
          + (" [DRY RUN]" if dry_run else ""))

    results_by_journal = {}

    for journal in JOURNALS:
        print(f"\n  Journal: {journal['name']}")
        seen_dois = load_seen_dois(journal["seen_file"])
        print(f"    {len(seen_dois)} previously seen DOIs")

        all_papers = fetch_latest_papers(journal["issn"], max_results=max_per_journal)
        if all_papers is None:
            print(f"    CrossRef unavailable — marking journal as unavailable this cycle")
            results_by_journal[journal["short"]] = None  # sentinel: server error
            continue
        print(f"    {len(all_papers)} papers fetched from CrossRef")

        new_papers = [p for p in all_papers if p["doi"] not in seen_dois]
        print(f"    {len(new_papers)} new papers")

        enriched = []
        for p in new_papers:
            print(f"    Processing: {p['title'][:70]}...")

            p["abstract"] = enrich_abstract(p["doi"], p["title"], p["abstract"], authors=p.get("authors", []))
            free_url, free_source = find_free_version(p["doi"], p["title"])
            p["free_url"] = free_url
            p["free_source"] = free_source

            if free_url and not dry_run:
                p["local_path"] = download_paper(
                    free_url, journal["short"], p["authors"], p["year"], p["title"], p["doi"]
                )
                filename = make_filename(p["authors"], p["year"], p["title"])
                p["web_url"] = paper_web_url(journal["short"], filename)
            else:
                p["local_path"] = None
                p["web_url"] = None

            raw_text = extract_pdf_text(p.get("local_path"))
            def _pdf_title_ok(text, title, threshold=0.3):
                t1 = set(re.sub(r"[^a-z0-9 ]", " ", re.sub(r"<[^>]+>", "", title).lower()).split())
                t1 -= {"and", "the", "of", "in", "a", "an", "to", "for", "on", "with", "by", "at", "is"}
                if not t1:
                    return True
                t2 = set(re.sub(r"[^a-z0-9 ]", " ", text[:3000].lower()).split())
                return len(t1 & t2) / len(t1) >= threshold

            if raw_text and _pdf_title_ok(raw_text, p["title"]):
                p["full_text"] = raw_text
                print(f"      PDF text extracted ({len(raw_text)} chars)")
            elif raw_text:
                print(f"      PDF text discarded — title mismatch (wrong paper downloaded)")
                p["full_text"] = ""
            else:
                p["full_text"] = ""
                print(f"      No PDF text — using abstract only")

            if p.get("is_corrigendum"):
                p["correction_note"] = analyze_corrigendum(p)
                p["analysis"] = {}
            else:
                p["analysis"] = analyze_paper(p, journal["name"])

            enriched.append(p)
            time.sleep(0.3)

        if enriched:
            results_by_journal[journal["short"]] = enriched

    total = sum(len(papers) for papers in results_by_journal.values())

    if total == 0:
        print("\nNothing new across all journals — no email sent.")
        return

    html = render_email_html(results_by_journal)
    text = render_email_text(results_by_journal)

    if dry_run:
        print("\n--- PLAIN TEXT PREVIEW ---\n")
        print(text)
        print("\n[Dry run: email NOT sent, seen files NOT updated]")
        return

    send_digest_email(html, text, total)

    # Persist seen DOIs only after successful send
    for journal in JOURNALS:
        papers = results_by_journal.get(journal["short"], [])
        for p in papers:
            mark_doi_seen(journal["seen_file"], p["doi"])

    print(f"\nDone. {total} papers processed and recorded.")


if __name__ == "__main__":
    import sys
    # Cron entry — runs every Saturday at 08:00:
    # 0 8 * * 6 cd /path/to/china_news && /usr/bin/python3 journal_agent.py >> /tmp/journal_digest.log 2>&1

    dry = "--dry-run" in sys.argv
    # --limit N processes only N papers per journal (useful for quick tests)
    limit = PAPERS_PER_JOURNAL
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    main(dry_run=dry, max_per_journal=limit)
