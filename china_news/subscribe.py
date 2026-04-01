from flask import Flask, request, jsonify
import logging
import re
import os
from flask_cors import CORS





app = Flask(__name__)
CORS(app, resources={r"/subscribe": {"origins": "*"}})

EMAIL_FILE = "subscribers.txt"

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

    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
