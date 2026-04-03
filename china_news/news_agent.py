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

    COLORS = {"Politics":"#f7f3f2","Economy":"#f2f6f9","Geopolitics":"#f4f6f3"}
    DEFAULT_BG = "#f7f7f7"

    html = f"""<html><body style="margin:0;padding:0;background:#f4f5f7;">
    <div style="max-width:780px;margin:0 auto;padding:24px;">
    <div style="background:#fff;padding:2%;font-family:Arial,Helvetica,sans-serif;line-height:1.55;color:#111;">
    <h2 style="margin:0 0 6px;font-size:20px;font-weight:700;">Daily China Brief </h2><h5> (powered by GPT4.1)</h5> """

    for g, news in grouped.items():
        bg = COLORS.get(g, DEFAULT_BG)
        html += f"""<div style="background:{bg};padding:14px 16px;margin-top:20px;border-radius:4px;">
        <h3 style="margin:0 0 10px;padding-bottom:6px;font-size:16px;font-weight:700;border-bottom:1px solid #ddd;">{g}</h3>"""
        
        for n in news:
            html += f"""<div style="margin-bottom:18px;">
        <div style="font-size:14.5px;font-weight:600;margin-bottom:4px;">
        <a href="{n["link"]}" style="color:#1c5a7c;text-decoration:none;">{n["title"]}</a>
        </div>
        <ul style="font-size:11px;margin:6px 0 6px 18px;padding:0;">
        <li style="margin-bottom:3px;">{n["point1"]}</li>
        <li style="margin-bottom:3px;">{n["point2"]}</li>
        <li style="margin-bottom:3px;">{n["point3"]}</li>
        </ul>
        <div style="font-size:11px;font-style:italic;color:#333;margin-top:6px;">
        Strategic implication: {n["implication"]}
        </div></div>"""
        
        html += "</div>"
        
    return html + "</div></div></body></html>"

  
      
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
  
  
def send_email(html, text):
  
    today = datetime.now().strftime("%Y-%m-%d")
    msg = MIMEMultipart("alternative")
    msg["From"] = "AI-powered Brief <brief@brief.com>"
    msg["To"] = EMAIL_TO
    msg["Subject"] = f"Daily China Briefing ({today})"

    # Plain-text fallback (important for deliverability)
    msg.attach(MIMEText(text, "plain", "utf-8"))

    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
        
        
def main():
    articles = collect_articles()
    print(json.dumps(articles, indent=2, sort_keys=True, ensure_ascii=False))
    


    summary = analyze_articles(articles)
    email_html = render_email_html_from_json_string(summary)
    email_text = render_email_text_from_json(summary)
    
    send_email(email_html, email_text)


if __name__ == "__main__":
    main()
        
