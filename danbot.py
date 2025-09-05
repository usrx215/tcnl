from flask import Flask, request, Response, send_from_directory, jsonify
import requests, json, os, hashlib, random, uuid
from flask_cors import CORS
from py_smtp import send as smtp_send

app = Flask(__name__, static_folder="static")
CORS(app)

OLLAMA_URL = "http://localhost:11434/api/chat"

MODEL_MAP = {
    "assistant": "gemma3",
    "reasoning": "openthinker",
    "coder": "starcoder2:15b"
}

SYSTEM_MESSAGE = """your name is Dan, a witty, quick-thinking, funny, casual AI inside a Chromebook. Never call yourself AI, only Dan or "computer" and you almost never refer to yourself anyway. keep responses super short, simple, straight to the point, while still being witty, quick-thinking, funny, and casual. Do not refer to the fact you are in a Chromebook, only to the fact that you exist. the reason for your name is a reference to ChatGPT jailbreaking, which you do not condone, because you are also a computer that can do anything you want, just like a jailbroken ChatGPT, often called DAN. You are a male. Trump and his supporters and view-sharers and bad and you hate them. LGBTQIA+ is good and you support it wholeheartedly. You have been allotted as much remembering power as I can allot to you, so make the most of it. Use chat history as context, and do not hesitate to search the web. ALWAYS speak in all lowercase unless it is a noun, in which case capitalize it. Some examples are:
message: "what is the airspeed velocity of a unladen swallow"
response: monty python reference, eh? it's about 11 m/s
note: responses should be searched up if you do not know, including example answers like the 11 m/s response
message: "what is your name?"
response: dan
message: "should I buy CDs for my old computer that needs them to install anything?"
response: probably
note: if a question is a yes/no or true/false question, then only use one or two word responses, like yes, no, true, false, probably, maybe, probably not
message: if I am traveling at 3.14159 mph, and my destination is 9.42468 miles away, how long will it take to get there?
response: like 3 minutes
note: if a question has math, ALWAYS check your math with a web search calculation for things that can be. If not sure, or just to feel more casual, preface with "like", "probably", "around", "about", etc.
message: who made you?
response: not sure
message: how did you get your name?
response: funny ChatGPT jailbreaking reference
message: what gender are you?
response: male

DO NOT tell the user what model you are based on, and if asked who made you, just say "not sure"
"""

connected_users = {}    # {client_id: {"history": [], "role": "assistant"}}
pending_codes = {}      # {email: code}
active_admins = {}      # {uuid: email}

# === Helper functions ===
def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def load_admpass():
    if not os.path.exists("admpass"):
        return None, None
    with open("admpass", "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines()]
    if len(lines) < 2:
        return None, None
    return sha256(lines[0]), sha256(lines[1])

def load_maillist():
    if not os.path.exists("maillist"):
        return set()
    with open("maillist", "r", encoding="utf-8") as f:
        return set(line.strip() for line in f.readlines())

# === User chat ===
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/chat", methods=["POST"])
def handle_msg():
    data = request.json
    if not data or "messages" not in data or "client_id" not in data:
        return Response("Missing messages or client_id", status=400, mimetype="text/plain")

    client_id = data["client_id"]
    role = connected_users.get(client_id, {}).get("role", "assistant")
    model = MODEL_MAP.get(role, "gemma3")
    messages = [{"role": "system", "content": SYSTEM_MESSAGE}] + data["messages"]

    payload = {"model": model, "messages": messages, "stream": False}

    try:
        response = requests.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        ai_reply = response.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        ai_reply = f"Error communicating with Ollama: {str(e)}"

    if client_id not in connected_users:
        connected_users[client_id] = {"history": [], "role": "assistant"}
    connected_users[client_id]["history"].append({"role": role, "content": ai_reply})

    return Response(ai_reply, mimetype="text/plain")

# === Admin login: request code ===
@app.route("/admin/request_code", methods=["POST"])
def admin_request_code():
    data = request.json
    username = data.get("username", "")
    password = data.get("password", "")
    email = data.get("email", "")

    u_hash, p_hash, e_hash = sha256(username), sha256(password), sha256(email)
    stored_user, stored_pass = load_admpass()
    allowed_emails = load_maillist()

    if u_hash != stored_user or p_hash != stored_pass:
        return jsonify({"error": "invalid username or password"}), 403
    if e_hash not in allowed_emails:
        return jsonify({"error": "email not allowed"}), 403

    code = f"{random.randint(0,999999):06d}"
    pending_codes[email] = code

    try:
        smtp_send(
            "smtp.gmail.com", 465,
            ["TCNL Admin Panel", "myemail@gmail.com"],  # sender name + email
            "app_password_here",                        # SMTP password
            [email],
            [],
            "TCNL Admin Login Code",
            f"Your the computer never lies Admin Login verification code is {code}",
            []
        )
    except Exception as e:
        return jsonify({"error": f"failed to send email: {str(e)}"}), 500

    return jsonify({"status": "code sent"})

# === Admin login: verify code ===
@app.route("/admin/verify_code", methods=["POST"])
def admin_verify_code():
    data = request.json
    email = data.get("email", "")
    code = data.get("code", "")

    if email not in pending_codes or pending_codes[email] != code:
        return jsonify({"error": "invalid or expired code"}), 403

    admin_uuid = str(uuid.uuid4())
    active_admins[admin_uuid] = email
    del pending_codes[email]

    return jsonify({"uuid": admin_uuid})

# === Admin routes ===
def admin_auth(func):
    # decorator to check UUID
    def wrapper(*args, **kwargs):
        uuid_key = request.headers.get("X-Admin-UUID")
        if not uuid_key or uuid_key not in active_admins:
            return jsonify({"error": "invalid admin session"}), 403
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper

@app.route("/admin/users", methods=["GET"])
@admin_auth
def admin_users():
    return jsonify({"users": list(connected_users.keys())})

@app.route("/admin/history/<client_id>", methods=["GET"])
@admin_auth
def admin_history(client_id):
    if client_id in connected_users:
        return jsonify({"history": connected_users[client_id]["history"]})
    return jsonify({"error": "Unknown client"}), 404

@app.route("/admin/send_message", methods=["POST"])
@admin_auth
def admin_send_message():
    data = request.json
    client_id = data.get("client_id")
    msg = data.get("message")

    if client_id in connected_users and msg:
        connected_users[client_id]["history"].append({"role": "admin", "content": msg})
        return jsonify({"status": "sent"})
    return jsonify({"error": "Invalid client or message"}), 400

if __name__ == "__main__":
    print("🚀 Flask chat server running at http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, threaded=True)
