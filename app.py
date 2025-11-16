from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_socketio import SocketIO, join_room, emit
import sqlite3
import os
import time
import uuid
from datetime import datetime

# ------------------ APP INIT ------------------
app = Flask(__name__)
app.secret_key = "some_random_secret"
socketio = SocketIO(app)

# ------------------ EMAIL SETTINGS (update these if you want working email) ------------------
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_USERNAME'] = 'saraggthebest246@gmail.com'   # change to your address
app.config['MAIL_PASSWORD'] = 'hzurvwmvwzodusdo'            # change to your app password
app.config['MAIL_DEFAULT_SENDER'] = 'saraggthebest246@gmail.com'
mail = Mail(app)

# ------------------ UPLOAD SETTINGS ------------------
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
CATEGORIES = ['Electronics', 'Clothes', 'Books', 'Furniture', 'Other']

# ------------------ BUILT-IN USERS ------------------
# These users bypass email sending / verification and can be used for testing/demo
BUILT_IN_USERS = {
    "eco_user": {"password": "password123", "email": "eco@example.com", "is_owner": True},
    "user1": {"password": "pass1", "email": "user1@example.com", "is_owner": False},
    "user2": {"password": "pass2", "email": "user2@example.com", "is_owner": False}
}

# ------------------ DATABASE ------------------
DB_FILE = "database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Items
    c.execute("""CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        category TEXT,
        location TEXT,
        image TEXT,
        claimed INTEGER DEFAULT 0,
        claimed_time INTEGER,
        giver TEXT,
        claimer TEXT
    )""")
    # Users
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        password TEXT,
        is_verified INTEGER DEFAULT 0,
        verification_token TEXT,
        bio TEXT DEFAULT '',
        avatar TEXT DEFAULT NULL,
        carbon_score REAL DEFAULT 0
    )""")
    # Messages
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        sender TEXT,
        receiver TEXT,
        message TEXT,
        timestamp INTEGER
    )""")
    # Notifications
    c.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        message TEXT,
        is_read INTEGER DEFAULT 0,
        timestamp INTEGER
    )""")
    # Achievements
    c.execute("""CREATE TABLE IF NOT EXISTS achievements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        description TEXT,
        timestamp INTEGER
    )""")

    # Insert built-in users into DB if missing (marked verified)
    for username, data in BUILT_IN_USERS.items():
        try:
            hashed_pw = generate_password_hash(data["password"])
            c.execute("INSERT INTO users (username, email, password, is_verified) VALUES (?, ?, ?, ?)",
                      (username, data["email"], hashed_pw, 1))
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()

init_db()

# ------------------ HELPERS ------------------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def calculate_carbon(category, action="post"):
    """
    Realistic-ish carbon savings per category.
    action == "post" -> awarding a portion for posting (saves some embodied emissions by re-use)
    action == "claim" -> full saving because claiming replaces buying new.
    Values are in kg CO2 equivalent.
    Adjust base_values if you'd like different numbers.
    """
    base_values = {
        "Electronics": 50.0,   # e.g. phone / small appliance
        "Clothes": 10.0,       # per garment
        "Books": 5.0,
        "Furniture": 30.0,     # small furniture
        "Other": 15.0
    }
    carbon = base_values.get(category, 10.0)
    if action == "post":
        # posting an item yields a smaller credited saving (encourages posting)
        return round(carbon * 0.2, 2)
    elif action == "claim":
        # claiming yields the larger (real) avoidance of producing a new item
        return round(carbon, 2)
    return 0.0

def get_unread_notifications(user):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM notifications WHERE user=? AND is_read=0", (user,))
    count = c.fetchone()[0]
    conn.close()
    return count

def get_latest_notifications(user, limit=5):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT message, timestamp FROM notifications WHERE user=? ORDER BY timestamp DESC LIMIT ?", (user, limit))
    rows = c.fetchall()
    conn.close()
    return rows

def get_messages(item_id):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT sender, receiver, message, timestamp FROM messages WHERE item_id=? ORDER BY timestamp ASC", (item_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def safe_send_mail(msg):
    """Wrapper to send mail but not crash on SMTP auth errors."""
    try:
        mail.send(msg)
    except Exception as e:
        # Print to console / log — do not raise so app keeps working
        print(f"[mail send failed] {e}")

# ------------------ JINJA / GLOBALS ------------------
@app.template_filter('datetimeformat')
def datetimeformat(value):
    return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M')

@app.context_processor
def inject_globals():
    # expose unread_count and calculate_carbon to templates
    return {
        "unread_count": get_unread_notifications(session["username"]) if "username" in session else 0,
        "calculate_carbon": calculate_carbon
    }

# ------------------ ROUTES ------------------
@app.route("/")
def index():
    area = request.args.get("area")
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if area and area.strip() != "":
        c.execute("SELECT * FROM items WHERE location LIKE ? ORDER BY id DESC", (f"%{area}%",))
    else:
        c.execute("SELECT * FROM items ORDER BY id DESC")
    items = c.fetchall()

    # Top users by carbon_score
    c.execute("SELECT username, avatar, carbon_score FROM users ORDER BY carbon_score DESC LIMIT 5")
    top_users = c.fetchall()

    conn.close()
    return render_template("index.html", items=items, area=area, top_users=top_users)

# ------------------ REGISTER ------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password']
        confirm = request.form.get('confirm', '')

        if not username or not email or not password:
            return "All fields required"
        if password != confirm:
            return "Passwords do not match"

        hashed_pw = generate_password_hash(password)
        verification_token = str(uuid.uuid4())

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, email, password, verification_token) VALUES (?, ?, ?, ?)",
                      (username, email, hashed_pw, verification_token))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return "Username or email already exists."
        conn.close()

        # Send verification email (wrapped to avoid app crash on SMTP problems)
        verify_link = url_for('verify_email', token=verification_token, _external=True)
        msg = Message(
            subject="Verify your Sustainable World account",
            recipients=[email],
            body=f"Hi {username},\n\nPlease verify your account by clicking the link below:\n{verify_link}\n\nThank you!"
        )
        safe_send_mail(msg)

        return "Registration successful! Check your email to verify your account."
    return render_template("register.html")

@app.route("/verify/<token>")
def verify_email(token):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE verification_token=? AND is_verified=0", (token,))
    row = c.fetchone()
    if row:
        user_id, username = row
        c.execute("UPDATE users SET is_verified=1, verification_token=NULL WHERE id=?", (user_id,))
        conn.commit()
        conn.close()
        return f"Email verified! You can now log in, {username}."
    conn.close()
    return "Invalid or expired verification link."

# ------------------ LOGIN / LOGOUT ------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]

        # built-in users bypass DB verify and email
        if username in BUILT_IN_USERS and BUILT_IN_USERS[username]["password"] == password:
            session["username"] = username
            return redirect("/")

        # Normal DB-backed login
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT password, is_verified FROM users WHERE username=?", (username,))
        row = c.fetchone()
        conn.close()
        if row and check_password_hash(row[0], password):
            if row[1] == 0:
                return "Please verify your email before logging in."
            session["username"] = username
            return redirect("/")
        return "Invalid username or password"
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect("/login")

# ------------------ PROFILE ------------------
@app.route("/profile/<username>", methods=["GET", "POST"])
def profile(username):
    if "username" not in session or session["username"] != username:
        return redirect("/login")

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if request.method == "POST":
        bio = request.form.get("bio", "")
        avatar_file = request.files.get("avatar")
        if avatar_file and allowed_file(avatar_file.filename):
            filename = secure_filename(avatar_file.filename)
            avatar_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            avatar_path = os.path.join('uploads', filename)
            c.execute("UPDATE users SET avatar=? WHERE username=?", (avatar_path, username))
        c.execute("UPDATE users SET bio=? WHERE username=?", (bio, username))
        conn.commit()

    c.execute("SELECT * FROM users WHERE username=?", (username,))
    user = c.fetchone()
    c.execute("SELECT * FROM achievements WHERE user=? ORDER BY timestamp DESC", (username,))
    achievements = c.fetchall()
    conn.close()
    return render_template("profile.html", user=user, achievements=achievements)

# ------------------ POST ITEM ------------------
@app.route("/post", methods=["GET", "POST"])
def post():
    if "username" not in session:
        return redirect("/login")
    if request.method == "POST":
        title = request.form["title"]
        description = request.form["description"]
        category = request.form["category"]
        location = request.form["location"]
        giver = session["username"]

        # Auto-calc carbon for posting
        carbon_saved = calculate_carbon(category, "post")

        file = request.files.get("image")
        filename = None
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            filename = os.path.join('uploads', filename)

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO items (title, description, category, location, image, giver) VALUES (?, ?, ?, ?, ?, ?)",
                  (title, description, category, location, filename, giver))
        # credit user with post carbon
        c.execute("UPDATE users SET carbon_score = carbon_score + ? WHERE username=?", (carbon_saved, giver))
        c.execute("INSERT INTO achievements (user, description, timestamp) VALUES (?, ?, ?)",
                  (giver, f"Posted an item and saved {carbon_saved:.1f} kg CO2", int(time.time())))
        conn.commit()
        conn.close()
        return redirect("/")
    return render_template("post.html", categories=CATEGORIES)

# ------------------ DELETE ITEM ------------------
@app.route("/delete/<int:item_id>")
def delete_item(item_id):
    # Owner can delete an item. If item is unclaimed, subtract the post carbon from owner's score.
    if "username" not in session:
        return redirect("/login")
    username = session["username"]

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, giver, claimed, category FROM items WHERE id=?", (item_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return redirect("/")

    _, giver, claimed, category = row
    # Only owner can delete (or allow admin? but we check owner)
    if giver != username and username not in BUILT_IN_USERS:
        conn.close()
        return "Unauthorized"

    if claimed == 0:
        # subtract the posting carbon that was credited earlier
        carbon_to_subtract = calculate_carbon(category, "post")
        c.execute("UPDATE users SET carbon_score = carbon_score - ? WHERE username=?", (carbon_to_subtract, giver))
        # Optionally remove achievement related to posting - here we keep achievements historically
    # Remove item and associated messages and notifications
    c.execute("DELETE FROM messages WHERE item_id=?", (item_id,))
    c.execute("DELETE FROM notifications WHERE message LIKE ?", (f"%item:{item_id}%",))  # cleanup pattern if used
    c.execute("DELETE FROM items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return redirect("/")

# ------------------ CLAIM ITEM ------------------
@app.route("/claim/<int:item_id>")
def claim(item_id):
    if "username" not in session:
        return redirect("/login")
    username = session["username"]
    timestamp = int(time.time())

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT giver, title, category, claimed FROM items WHERE id=? AND claimed=0", (item_id,))
    item = c.fetchone()
    if item:
        giver, title, category, _ = item
        c.execute("UPDATE items SET claimed=1, claimed_time=?, claimer=? WHERE id=?",
                  (timestamp, username, item_id))
        notif_msg = f"{username} has claimed your item: {title}"
        c.execute("INSERT INTO notifications (user, message, is_read, timestamp) VALUES (?, ?, 0, ?)",
                  (giver, notif_msg, timestamp))

        # If giver is a DB user and verified, try to email them; built-in users bypass sending
        if giver not in BUILT_IN_USERS:
            c.execute("SELECT email, is_verified FROM users WHERE username=?", (giver,))
            row = c.fetchone()
            if row and row[1] == 1:
                msg = Message(subject="Your item has been claimed", recipients=[row[0]], body=notif_msg)
                safe_send_mail(msg)

        # Carbon saved for claimer (full saving)
        carbon_saved = calculate_carbon(category, "claim")
        c.execute("UPDATE users SET carbon_score = carbon_score + ? WHERE username=?", (carbon_saved, username))
        c.execute("INSERT INTO achievements (user, description, timestamp) VALUES (?, ?, ?)",
                  (username, f"Claimed an item and saved {carbon_saved:.1f} kg CO2", timestamp))

        # Emit notification via Socket.IO
        socketio.emit("new_notification", {"user": giver, "message": notif_msg,
                                           "time": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")})
        conn.commit()
    conn.close()
    return redirect("/")

# ------------------ MESSAGES ------------------
@app.route("/messages/<int:item_id>", methods=["GET"])
def messages_page(item_id):
    if "username" not in session:
        return redirect("/login")
    username = session["username"]

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM items WHERE id=?", (item_id,))
    item = c.fetchone()
    if not item:
        conn.close()
        return "Item not found"

    # Only giver + claimer are allowed to view messages (and only after claim, messaging is meaningful)
    if item['claimed'] == 0:
        # before claim: only giver (owner) can view messages (owner might want to message interested people)
        if username != item['giver']:
            conn.close()
            return "Unauthorized"
    else:
        # after claim: only giver or claimer can view
        if username not in (item['giver'], item['claimer']):
            conn.close()
            return "Unauthorized"

    messages = get_messages(item_id)
    conn.close()
    return render_template("messages.html", item=item, username=username, messages=messages)

@socketio.on("join")
def on_join(data):
    room = data.get("room")
    join_room(room)

@socketio.on("send_message")
def handle_message(data):
    # data: { room: item_id, sender: username, message: text }
    item_id = data.get("room")
    message = data.get("message")
    sender = data.get("sender")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT giver, claimer, claimed FROM items WHERE id=?", (item_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return

    giver, claimer, claimed = row
    # Determine receiver: if sender is giver, receiver is claimer (if claimed), else receiver is giver
    if sender == giver:
        receiver = claimer if claimer else None
    else:
        receiver = giver

    # If no receiver (owner messaging before claim to unknown claimer), we still store message with receiver NULL
    timestamp = int(time.time())
    c.execute("INSERT INTO messages (item_id, sender, receiver, message, timestamp) VALUES (?, ?, ?, ?, ?)",
              (item_id, sender, receiver or "", message, timestamp))

    notif_msg = f"New message from {sender} on item {item_id}"
    if receiver:
        c.execute("INSERT INTO notifications (user, message, is_read, timestamp) VALUES (?, ?, 0, ?)",
                  (receiver, notif_msg, timestamp))

        # Send email unless receiver is built-in user
        if receiver not in BUILT_IN_USERS:
            c.execute("SELECT email, is_verified FROM users WHERE username=?", (receiver,))
            r = c.fetchone()
            if r and r[1] == 1:
                msg = Message(subject="New message notification", recipients=[r[0]], body=notif_msg)
                safe_send_mail(msg)

    # Emit a notification event (frontend listens to 'new_notification')
    socketio.emit("new_notification", {"user": receiver or giver, "message": notif_msg,
                                       "time": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")})
    conn.commit()
    conn.close()

    # Deliver the chat message in the room so clients see it instantly
    emit("receive_message", {"sender": sender, "message": message}, room=item_id)

# ------------------ NOTIFICATIONS PAGE ------------------
@app.route("/notifications")
def notifications_page():
    if "username" not in session:
        return redirect("/login")
    username = session["username"]
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, message, is_read, timestamp FROM notifications WHERE user=? ORDER BY timestamp DESC", (username,))
    notifications = c.fetchall()
    c.execute("UPDATE notifications SET is_read = 1 WHERE user=?", (username,))
    conn.commit()
    conn.close()
    return render_template("notifications.html", notifications=notifications)

# ------------------ RUN ------------------
if __name__ == "__main__":
    socketio.run(app, debug=True)
