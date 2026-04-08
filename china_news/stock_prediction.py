import feedparser
import json
import smtplib
import os
from datetime import datetime, timedelta
from openai import OpenAI
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GMAIL_USER = os.getenv("EMAIL_FROM", "georgeyean@gmail.com")
GMAIL_PASS = os.getenv("EMAIL_PASS")

client = OpenAI(api_key=OPENAI_API_KEY)

FINANCE_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2d/headline?s=%5EGSPC&region=US&lang=en-US",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
]


def collect_market_news():
    articles = []
    cutoff = datetime.utcnow() - timedelta(days=1)

    for url in FINANCE_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                pub = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub = datetime(*entry.published_parsed[:6])
                if pub and pub < cutoff:
                    continue
                articles.append({
                    "title": entry.get("title", "").strip(),
                    "summary": entry.get("summary", "").strip()[:300],
                    "link": entry.get("link", ""),
                })
        except Exception as e:
            print(f"Feed error ({url}): {e}")

    return articles


def analyze_market(articles):
    news_text = "\n".join(
        f"- {a['title']}: {a['summary']}" for a in articles
    )

    prompt = f"""You are a senior equity strategist. Based on today's financial news, provide:

1. **Today's Prediction**: Your prediction for today's S&P 500 trading session. Include expected direction, predicted percentage move range, key factors driving today's session, and a confidence level (high/medium/low).

2. **Market Movers**: What news drove today's S&P 500 movement? For each, state the event, whether it was bullish or bearish, and a one-sentence explanation.

3. **5-Day Outlook**: Your prediction for the next 5 trading days based on real events and their likely development. Include overall direction, reasoning, key risks, and key catalysts.

Be specific, cite actual news, and give clear directional reasoning.

Today's news:
{news_text}

Return ONLY valid JSON in this exact format:
{{
  "today_prediction": {{
    "direction": "bullish/bearish/neutral",
    "predicted_move": "+0.X% to +0.Y%",
    "confidence": "high/medium/low",
    "key_factors": ["..."],
    "summary": "..."
  }},
  "market_movers": [
    {{"event": "...", "impact": "bullish/bearish", "explanation": "..."}}
  ],
  "five_day_outlook": {{
    "direction": "bullish/bearish/neutral",
    "reasoning": "...",
    "key_risks": ["..."],
    "key_catalysts": ["..."]
  }}
}}"""

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "You are a senior equity strategist. Be concise, data-driven, and specific. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )

    return response.choices[0].message.content


def validate_market_analysis(json_str):
    try:
        s = json_str.strip()
        if s.startswith("```"):
            s = s.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(s)

        if "market_movers" not in data or "five_day_outlook" not in data or "today_prediction" not in data:
            return False, "Missing required keys", None
        if not isinstance(data["market_movers"], list) or len(data["market_movers"]) == 0:
            return False, "No market movers found", None

        return True, None, data

    except (json.JSONDecodeError, ValueError) as e:
        return False, f"Invalid JSON: {e}", None


def render_market_email_html(data):
    today = datetime.now().strftime("%B %d, %Y")

    # Today's Prediction section
    tp = data.get("today_prediction", {})
    tp_direction = tp.get("direction", "N/A").lower()
    if "bull" in tp_direction:
        tp_color, tp_bg = "#16a34a", "#f0fdf4"
        tp_arrow = "▲"
    elif "bear" in tp_direction:
        tp_color, tp_bg = "#dc2626", "#fef2f2"
        tp_arrow = "▼"
    else:
        tp_color, tp_bg = "#d97706", "#fffbeb"
        tp_arrow = "►"

    conf = tp.get("confidence", "N/A").upper()
    conf_colors = {"HIGH": "#16a34a", "MEDIUM": "#d97706", "LOW": "#dc2626"}
    conf_color = conf_colors.get(conf, "#666")

    tp_factors_html = "".join(f'<li style="margin-bottom:4px;">{f}</li>' for f in tp.get("key_factors", []))

    today_prediction_html = f"""
      <div style="margin-bottom:12px;padding:16px;background:{tp_bg};border-radius:8px;border:2px solid {tp_color};">
        <div style="text-align:center;margin-bottom:12px;">
          <span style="font-size:20px;font-weight:700;color:{tp_color};">{tp_arrow} {tp_direction.upper()}</span>
          <span style="display:inline-block;margin-left:12px;font-size:16px;font-weight:600;color:#333;">{tp.get('predicted_move', 'N/A')}</span>
        </div>
        <div style="text-align:center;margin-bottom:12px;">
          <span style="font-size:12px;font-weight:700;color:{conf_color};background:#fff;padding:2px 10px;border-radius:4px;">Confidence: {conf}</span>
        </div>
        <p style="font-size:14px;color:#444;line-height:1.6;margin:0 0 8px;">{tp.get('summary', '')}</p>
        <ul style="font-size:13px;color:#444;margin:8px 0 0 16px;padding:0;">{tp_factors_html}</ul>
      </div>"""

    movers_html = ""
    for m in data.get("market_movers", []):
        is_bull = m["impact"].lower() == "bullish"
        color = "#16a34a" if is_bull else "#dc2626"
        arrow = "▲" if is_bull else "▼"
        movers_html += f"""
      <div style="margin-bottom:12px;padding:12px;background:#fafafa;border-radius:6px;border-left:3px solid {color};">
        <div style="font-size:14px;font-weight:600;color:#1a1a2e;margin-bottom:4px;">{m['event']}</div>
        <span style="display:inline-block;font-size:12px;font-weight:700;color:{color};background:{'#f0fdf4' if is_bull else '#fef2f2'};padding:2px 8px;border-radius:4px;margin-bottom:6px;">{arrow} {m['impact'].upper()}</span>
        <p style="font-size:13px;color:#444;margin:6px 0 0;">{m['explanation']}</p>
      </div>"""

    outlook = data.get("five_day_outlook", {})
    direction = outlook.get("direction", "N/A").lower()
    if "bull" in direction:
        dir_color, dir_bg = "#16a34a", "#f0fdf4"
    elif "bear" in direction:
        dir_color, dir_bg = "#dc2626", "#fef2f2"
    else:
        dir_color, dir_bg = "#d97706", "#fffbeb"

    risks_html = "".join(f'<li style="margin-bottom:4px;">{r}</li>' for r in outlook.get("key_risks", []))
    catalysts_html = "".join(f'<li style="margin-bottom:4px;">{c}</li>' for c in outlook.get("key_catalysts", []))

    html = f"""<html><body style="margin:0;padding:0;background:#f0f0f0;">
    <div style="max-width:600px;margin:0 auto;padding:16px;">
    <div style="background:#ffffff;border-radius:8px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#222;">

    <div style="background:#0f172a;padding:24px 20px;text-align:center;">
      <h1 style="margin:0;font-size:22px;font-weight:700;color:#ffffff;">S&P 500 Daily Briefing</h1>
      <p style="margin:6px 0 0;font-size:13px;color:#8e8ea0;">{today} · AI-powered market analysis</p>
    </div>

    <div style="padding:20px;">

      <h2 style="font-size:16px;color:#0f172a;margin:0 0 12px;padding-bottom:8px;border-bottom:2px solid {tp_color};">Today's Market Prediction</h2>
      {today_prediction_html}

      <h2 style="font-size:16px;color:#0f172a;margin:24px 0 12px;padding-bottom:8px;border-bottom:2px solid #2563eb;">Market Movers</h2>
      {movers_html}

      <h2 style="font-size:16px;color:#0f172a;margin:24px 0 12px;padding-bottom:8px;border-bottom:2px solid {dir_color};">5-Day Outlook</h2>
      <div style="text-align:center;margin-bottom:12px;">
        <span style="display:inline-block;font-size:18px;font-weight:700;color:{dir_color};background:{dir_bg};padding:8px 24px;border-radius:8px;">{direction.upper()}</span>
      </div>
      <p style="font-size:14px;color:#444;line-height:1.6;">{outlook.get('reasoning', '')}</p>

      <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;">
        <tr>
          <td width="50%" valign="top" style="padding-right:8px;">
            <div style="background:#fef2f2;padding:12px;border-radius:8px;">
              <strong style="color:#dc2626;font-size:13px;">Key Risks</strong>
              <ul style="font-size:13px;color:#444;margin:8px 0 0 16px;padding:0;">{risks_html}</ul>
            </div>
          </td>
          <td width="50%" valign="top" style="padding-left:8px;">
            <div style="background:#f0fdf4;padding:12px;border-radius:8px;">
              <strong style="color:#16a34a;font-size:13px;">Key Catalysts</strong>
              <ul style="font-size:13px;color:#444;margin:8px 0 0 16px;padding:0;">{catalysts_html}</ul>
            </div>
          </td>
        </tr>
      </table>

    </div>

    <div style="background:#f8f8f8;padding:16px 20px;text-align:center;border-top:1px solid #eee;">
      <p style="margin:0;font-size:11px;color:#999;">AI-generated analysis · Not financial advice · Powered by GPT-4.1</p>
    </div>

    </div></div></body></html>"""

    return html


def render_market_email_text(data):
    text = "S&P 500 Daily Briefing\n\n"

    tp = data.get("today_prediction", {})
    text += "TODAY'S MARKET PREDICTION\n" + "=" * 40 + "\n"
    text += f"Direction: {tp.get('direction', 'N/A').upper()}\n"
    text += f"Predicted Move: {tp.get('predicted_move', 'N/A')}\n"
    text += f"Confidence: {tp.get('confidence', 'N/A').upper()}\n"
    text += f"{tp.get('summary', '')}\n"
    text += "Key Factors:\n" + "\n".join(f"  - {f}" for f in tp.get("key_factors", [])) + "\n"

    text += "\n\nMARKET MOVERS\n" + "=" * 40 + "\n"
    for m in data.get("market_movers", []):
        text += f"\n{m['event']} [{m['impact'].upper()}]\n{m['explanation']}\n"

    outlook = data.get("five_day_outlook", {})
    text += f"\n\n5-DAY OUTLOOK: {outlook.get('direction', 'N/A').upper()}\n" + "=" * 40 + "\n"
    text += f"{outlook.get('reasoning', '')}\n\n"
    text += "Key Risks:\n" + "\n".join(f"  - {r}" for r in outlook.get("key_risks", [])) + "\n\n"
    text += "Key Catalysts:\n" + "\n".join(f"  - {c}" for c in outlook.get("key_catalysts", [])) + "\n"
    text += "\n---\nAI-generated analysis. Not financial advice.\n"

    return text


def send_market_email(html, text):
    today = datetime.now().strftime("%Y-%m-%d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"S&P 500 Daily Briefing ({today})"
    msg["From"] = f"Market Brief <{GMAIL_USER}>"
    msg["To"] = "georgeyean@gmail.com"

    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)
    print("S&P 500 briefing sent to georgeyean@gmail.com")


def send_market_failure_alert(reason, raw_response=""):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"Market Brief <{GMAIL_USER}>"
        msg["To"] = "georgeyean@gmail.com"
        msg["Subject"] = f"S&P 500 Brief FAILED - {datetime.now().strftime('%Y-%m-%d')}"

        html = f"""\
        <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
          <h2 style="color:#c0392b;">S&P 500 Brief Failed</h2>
          <p><strong>Reason:</strong> {reason}</p>
          <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
          <details style="margin-top:16px;">
            <summary style="cursor:pointer;font-weight:600;">Raw AI Response</summary>
            <pre style="background:#f5f5f5;padding:12px;border-radius:4px;font-size:12px;overflow-x:auto;white-space:pre-wrap;">{raw_response[:3000]}</pre>
          </details>
        </div>"""

        msg.attach(MIMEText(html, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
        print("Market failure alert sent")
    except Exception as e:
        print(f"Failed to send market alert: {e}")


def sp500_daily_briefing():
    print("Starting S&P 500 briefing...")

    articles = collect_market_news()
    print(f"Collected {len(articles)} market articles")

    if not articles:
        send_market_failure_alert("No market articles collected from RSS feeds")
        return

    raw_analysis = analyze_market(articles)

    valid, reason, data = validate_market_analysis(raw_analysis)
    if not valid:
        print(f"AI returned invalid content: {reason}")
        send_market_failure_alert(reason, raw_analysis)
        return

    html = render_market_email_html(data)
    text = render_market_email_text(data)

    send_market_email(html, text)
    print("S&P 500 briefing done.")


if __name__ == "__main__":
    sp500_daily_briefing()
