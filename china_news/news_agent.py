# library(reticulate)
# py_install(
#   packages = c("openai", "requests", "feedparser", "python-dotenv", "markdown"),
#   pip = TRUE
# )

import feedparser
from datetime import datetime, timedelta
from openai import OpenAI
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import markdown
import json
import os, pdb
from dotenv import load_dotenv

load_dotenv()
from collections import defaultdict


OPENAI_API_KEY=os.getenv("OPENAI_API_KEY")
EMAIL_USER='georgeyean@gmail.com'
EMAIL_PASS=os.getenv("EMAIL_PASS")
EMAIL_TO='georgeyean@gmail.com'



RSS_FEEDS = [
    "https://www.reuters.com/world/china/rss",
    "https://www.ft.com/china?format=rss",
    "https://asia.nikkei.com/rss/feed/nar",
    "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
    "https://www.scmp.com/rss/91/feed",
    "https://www.csis.org/rss.xml",
    "https://www.rand.org/rss.html",
    "https://www.piie.com/rss"
]

def get_entry_datetime(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6])
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return datetime(*entry.updated_parsed[:6])
    return None


def collect_articles():
    articles = []
    cutoff = datetime.utcnow() - timedelta(days=1)

    for feed in RSS_FEEDS:
        parsed = feedparser.parse(feed)

        for entry in parsed.entries:
            entry_time = get_entry_datetime(entry)

            # If no timestamp, keep it (better recall than precision)
            if entry_time and entry_time < cutoff:
                continue
            articles.append({
                "title": entry.get("title", "").strip(),
                "summary": entry.get("summary", "").strip(),
                "link": entry.get("link", "")
            })

    return articles



client = OpenAI(api_key=OPENAI_API_KEY)

def analyze_articles(articles):
    content = "\n\n".join(
        f"Title: {a['title']}\nSummary: {a['summary']}\nLink: {a['link']}"
        for a in articles
    )

    prompt = (
        "You are an analyst specializing in Chinese politics, economy, and geopolitics.\n\n"
        "From the following news (past 24 hours):\n"
        "1. Group news into Politics, Economy, Geopolitics\n"
        "2. Extract ALL China-related developments but remove duplicated news (downplay SCMP if duplicated); add which press the news is from in a bracket BEFORE title\n"
        "3. Go to link for each news to extract three key points in the text; return 'PW' if paywalled\n"
        "4. Explain strategic implications, not just facts (1 sentence max) for each news\n"
        "5. Add link to news in the end for each news"
        "6. Merge all steps together, not seperate display; Be concise, analytical, neutral\n\n"
        "Return JSON format only, containing: group, title, point1, point2, point3, implication, link. \n"
        f"News:\n{content}"
    )

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    return response.choices[0].message.content




def render_email_html_from_json_string(json_str: str) -> str:
    s = json_str.strip()
    items = json.loads(s[s.find("["):s.rfind("]")+1])

    grouped = defaultdict(list)
    for i in items:
        grouped[i["group"]].append(i)

    today = datetime.now().strftime("%B %d, %Y")

    ICONS = {"Politics": "🏛", "Economy": "📊", "Geopolitics": "🌏"}
    ACCENTS = {"Politics": "#c0392b", "Economy": "#2471a3", "Geopolitics": "#1e8449"}
    DEFAULT_ACCENT = "#555"

    html = f"""<html><body style="margin:0;padding:0;background:#f0f0f0;">
    <div style="max-width:600px;margin:0 auto;padding:16px;">
    <div style="background:#ffffff;border-radius:8px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#222;">

    <!-- Header -->
    <div style="background:#1a1a2e;padding:24px 20px;text-align:center;">
      <h1 style="margin:0;font-size:22px;font-weight:700;color:#ffffff;letter-spacing:0.5px;">China Brief</h1>
      <p style="margin:6px 0 0;font-size:13px;color:#8e8ea0;">{today} · AI-powered daily briefing</p>
    </div>

    <div style="padding:20px;">"""

    for g, news in grouped.items():
        accent = ACCENTS.get(g, DEFAULT_ACCENT)
        icon = ICONS.get(g, "📌")

        html += f"""
    <div style="margin-bottom:24px;">
      <div style="display:flex;align-items:center;margin-bottom:12px;padding-bottom:8px;border-bottom:2px solid {accent};">
        <span style="font-size:18px;margin-right:8px;">{icon}</span>
        <h2 style="margin:0;font-size:16px;font-weight:700;color:{accent};text-transform:uppercase;letter-spacing:0.5px;">{g}</h2>
      </div>"""

        for n in news:
            html += f"""
      <div style="margin-bottom:16px;padding:12px;background:#fafafa;border-radius:6px;border-left:3px solid {accent};">
        <a href="{n["link"]}" style="font-size:15px;font-weight:600;color:#1a1a2e;text-decoration:none;line-height:1.3;">{n["title"]}</a>
        <ul style="font-size:13px;color:#444;margin:8px 0 8px 16px;padding:0;line-height:1.5;">
          <li style="margin-bottom:4px;">{n["point1"]}</li>
          <li style="margin-bottom:4px;">{n["point2"]}</li>
          <li style="margin-bottom:4px;">{n["point3"]}</li>
        </ul>
        <p style="font-size:12px;color:#666;margin:8px 0 0;padding-top:6px;border-top:1px solid #eee;font-style:italic;">
          ⚡ {n["implication"]}
        </p>
      </div>"""

        html += "</div>"

    html += """
    </div>

    <!-- Footer -->
    <div style="background:#f8f8f8;padding:16px 20px;text-align:center;border-top:1px solid #eee;">
      <p style="margin:0;font-size:11px;color:#999;line-height:1.5;">
        China Brief · AI-powered news analysis · Powered by GPT-4.1
      </p>
    </div>

    </div></div></body></html>"""

    return html

  
      
def render_email_text_from_json(json_str):
  
    json_str = json_str.strip()
    start = json_str.find("[")
    end = json_str.rfind("]") + 1
    items = json.loads(json_str[start:end])
    
    grouped = defaultdict(list)
    for item in items:
        grouped[item["group"]].append(item)

    text = f"Daily China Brief\n\n"

    for group, news in grouped.items():
        text += f"{group}\n{'-'*len(group)}\n"

        for n in news:
            text += f"{n['title']}\n"
            text += f"- {n['point1']}\n"
            text += f"- {n['point2']}\n"
            text += f"- {n['point3']}\n"
            text += f"Strategic implication: {n['implication']}\n"
            text += f"Source: {n['link']}\n\n"

    return text
  
  
def get_subscribers(list_name="china-daily"):
    """Read subscriber emails from the list file."""
    filepath = os.path.join("subscribers", f"{list_name}.txt")
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return [line.strip().lower() for line in f if line.strip()]


def send_email(html, text):
    subscribers = get_subscribers("china-daily")
    if not subscribers:
        print("No subscribers found for china-daily")
        return

    today = datetime.now().strftime("%Y-%m-%d")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASS)

        for recipient in subscribers:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"China Brief <{EMAIL_USER}>"
            msg["To"] = recipient
            msg["Subject"] = f"Daily China Briefing ({today})"

            msg.attach(MIMEText(text, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))

            try:
                server.send_message(msg)
                print(f"Sent to {recipient}")
            except Exception as e:
                print(f"Failed to send to {recipient}: {e}")
        
        
def main():
    articles = collect_articles()
    print(json.dumps(articles, indent=2, sort_keys=True, ensure_ascii=False))
    


    summary = analyze_articles(articles)
    email_html = render_email_html_from_json_string(summary)
    email_text = render_email_text_from_json(summary)
    
    send_email(email_html, email_text)


if __name__ == "__main__":
    main()
        
