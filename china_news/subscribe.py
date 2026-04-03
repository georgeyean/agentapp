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

EMAIL_FILE = "subscribers.txt"
GMAIL_USER = os.getenv("EMAIL_FROM", "georgeyean@gmail.com")
GMAIL_PASS = os.getenv("EMAIL_PASS")


def send_confirmation(to_email):
    html = """\
    <div style="font-family: Georgia, serif; max-width: 520px; margin: 0 auto; padding: 40px 20px;">
      <h2 style="font-size: 22px; color: #1a1a1a; margin-bottom: 24px;">
        You're subscribed.
      </h2>
      <p style="font-size: 16px; line-height: 1.6; color: #333;">
        You'll receive daily briefings on the latest China news,
        powered by AI.
      </p>
      <hr style="border: none; border-top: 1px solid #ddd; margin: 32px 0;" />
      <p style="font-size: 13px; color: #999;">
        China Brief
      </p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Welcome to China Brief"
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

    # Validation
    if not is_valid_email(email):
        print("Invalid email")
        return jsonify({"error": "Invalid email"}), 400

    # Prevent newline / file injection
    if "\n" in email or "\r" in email:
        print("Invalid characters")
        return jsonify({"error": "Invalid characters"}), 400

    # Ensure directory-safe append
    try:
        with open(EMAIL_FILE, "a", encoding="utf-8") as f:
            f.write(email + "\n")
    except OSError:
        print("Server write error")
        return jsonify({"error": "Server write error"}), 500

    try:
        send_confirmation(email)
    except Exception as e:
        app.logger.error(f"Confirmation email failed: {e}")

    return jsonify({"success": True}), 200


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
