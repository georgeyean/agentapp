from flask import Flask, request, jsonify
import logging
import re
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask_cors import CORS



app = Flask(__name__)
CORS(app, resources={r"/subscribe": {"origins": "*"}})

SUBSCRIBE_DIR = "subscribers"
GMAIL_USER = os.getenv("EMAIL_FROM", "georgeyean@gmail.com")
GMAIL_PASS = os.getenv("EMAIL_PASS")


def get_list_file(list_type):
    """Return the file path for a given subscription list."""
    # Sanitize: only allow alphanumeric, hyphens, underscores
    safe_name = re.sub(r"[^a-z0-9_-]", "", list_type.lower())
    if not safe_name:
        safe_name = "default"
    os.makedirs(SUBSCRIBE_DIR, exist_ok=True)
    return os.path.join(SUBSCRIBE_DIR, f"{safe_name}.txt")


def send_confirmation(to_email, list_type):
    html = f"""\
    <div style="font-family: Georgia, serif; max-width: 520px; margin: 0 auto; padding: 40px 20px;">
      <h2 style="font-size: 22px; color: #1a1a1a; margin-bottom: 24px;">
        You're subscribed.
      </h2>
      <p style="font-size: 16px; line-height: 1.6; color: #333;">
        You've been added to <strong>{list_type}</strong>. You'll receive
        briefings powered by AI.
      </p>
      <hr style="border: none; border-top: 1px solid #ddd; margin: 32px 0;" />
      <p style="font-size: 13px; color: #999;">
        China Brief
      </p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Welcome to {list_type.title()} Brief "
    msg["From"] = f"China Brief <{GMAIL_USER}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)

# strict but reasonable email regex
EMAIL_REGEX = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
)

def is_valid_email(email: str) -> bool:
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_REGEX.match(email))


@app.route("/subscribe", methods=["POST"])
def subscribe():
    app.logger.warning("SUBSCRIBE HIT")
    app.logger.warning(request.headers)

    # Require JSON
    if not request.is_json:
        print("not request.is_json")
        return jsonify({"error": "JSON body required"}), 400

    data = request.get_json()
    email = data.get("email", "").strip().lower()
    list_type = data.get("list", "default").strip().lower()

    # Validation
    if not is_valid_email(email):
        print("Invalid email")
        return jsonify({"error": "Invalid email"}), 400

    # Prevent newline / file injection
    if "\n" in email or "\r" in email:
        print("Invalid characters")
        return jsonify({"error": "Invalid characters"}), 400

    email_file = get_list_file(list_type)

    # Check if already subscribed to this list
    try:
        if os.path.exists(email_file):
            with open(email_file, "r", encoding="utf-8") as f:
                existing = {line.strip().lower() for line in f if line.strip()}
            if email in existing:
                return jsonify({"error": f"Already subscribed to {list_type}"}), 409
    except OSError:
        pass

    # Append to list file
    try:
        with open(email_file, "a", encoding="utf-8") as f:
            f.write(email + "\n")
    except OSError:
        print("Server write error")
        return jsonify({"error": "Server write error"}), 500

    try:
        send_confirmation(email, list_type)
    except Exception as e:
        app.logger.error(f"Confirmation email failed: {e}")

    return jsonify({"success": True}), 200


@app.route("/subscribers", methods=["GET"])
def view_subscribers():
    lists = {}
    if os.path.isdir(SUBSCRIBE_DIR):
        for fname in sorted(os.listdir(SUBSCRIBE_DIR)):
            if fname.endswith(".txt"):
                list_name = fname[:-4]
                fpath = os.path.join(SUBSCRIBE_DIR, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        emails = [line.strip() for line in f if line.strip()]
                except OSError:
                    emails = []
                lists[list_name] = emails

    rows = ""
    total = 0
    for list_name, emails in lists.items():
        total += len(emails)
        rows += f'<tr><td colspan="2" style="background:#f0f0f0;font-weight:bold;padding:8px 12px">{list_name} ({len(emails)})</td></tr>'
        for i, email in enumerate(emails, 1):
            rows += f'<tr><td style="padding:6px 12px;color:#888;width:40px">{i}</td><td style="padding:6px 12px">{email}</td></tr>'

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Subscribers</title>
  <style>
    body {{ font-family: Georgia, serif; max-width: 640px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; }}
    h1 {{ font-size: 22px; margin-bottom: 4px; }}
    p.sub {{ color: #888; font-size: 14px; margin-bottom: 24px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    tr:not(:last-child) td {{ border-bottom: 1px solid #eee; }}
  </style>
</head>
<body>
  <h1>Subscribers</h1>
  <p class="sub">Total: {total}</p>
  <table>{rows}</table>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
  
    #sudo vi /etc/systemd/system/subscribe.service
    
    #sudo systemctl daemon-reload
    #sudo systemctl reset-failed subscribe
    #sudo systemctl restart subscribe
    #sudo systemctl status subscribe
  
    app.run(host="0.0.0.0", port=80, debug=True)
