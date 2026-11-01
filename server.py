"""
Steve Central's Images - backend server.

Zero-dependency: uses only the Python standard library (http.server + sqlite3).
Serves the existing static site AND provides a small JSON API plus an admin view.

Run with:   py server.py           (defaults to port 8000)
            py server.py 8080      (custom port)

Routes:
  GET  /                        -> index.html (static)
  GET  /<file>                  -> any static file in this folder (html/css/images)
  GET  /api/products            -> JSON list of packages and prices
  GET  /api/gallery             -> JSON list of gallery image filenames
  POST /api/contact             -> save a contact message   {fname,email,phone,comments}
  POST /api/orders              -> save an order            {name,email,package,price}
  GET  /api/contacts            -> JSON contact messages (admin)
  GET  /api/orders              -> JSON orders (admin)
  POST /api/contacts/delete     -> delete a contact by id (admin)  {id}
  POST /api/orders/delete       -> delete an order by id (admin)   {id}
  POST /api/orders/handled      -> set order handled flag (admin)  {id, handled}
  GET  /admin                   -> HTML dashboard (admin)
  GET  /admin/export/contacts.csv -> CSV download (admin)
  GET  /admin/export/orders.csv   -> CSV download (admin)
"""

import base64
import csv
import html
import io
import json
import os
import smtplib
import sqlite3
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Image files that are part of the layout, not gallery photos.
GALLERY_EXCLUDE = {"logo.jpg", "background.svg", "Price.jpg"}
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")

DB_FILE = "steve.db"

# Admin credentials for the /admin dashboard and admin JSON endpoints.
# Override via environment variables ADMIN_USER / ADMIN_PASS.
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "steve2026")

# Read-only demo login (for recruiters/portfolio viewers): can VIEW the admin
# dashboard and data, but cannot delete, approve, or change anything.
DEMO_USER = os.environ.get("DEMO_USER", "demo")
DEMO_PASS = os.environ.get("DEMO_PASS", "demo")

# Simple in-memory rate limit for public form submissions (spam guard):
# at most RATE_MAX submissions per IP per RATE_WINDOW seconds.
RATE_MAX = int(os.environ.get("RATE_MAX", "6"))
RATE_WINDOW = int(os.environ.get("RATE_WINDOW", "60"))
_rate_hits = {}

# ---- Email notifications --------------------------------------------------
# Every contact message and order is emailed to NOTIFY_TO.
# To actually send, set SMTP_USER and SMTP_PASS (a Yahoo *app password*, not
# the normal login password) as environment variables. Nothing is hard-coded.
#
#   PowerShell:
#     $env:SMTP_USER = "youraccount@yahoo.com"
#     $env:SMTP_PASS = "your-app-password"
#     py server.py 8000
#
# If SMTP_USER/SMTP_PASS are not set, submissions are still saved to the
# database and shown in /admin; only the email step is skipped.
NOTIFY_TO = os.environ.get("NOTIFY_TO", "robertsteeved@yahoo.com")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.mail.yahoo.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
# Yahoo shows app passwords in 4 space-separated groups; the actual password
# has no spaces, so strip any whitespace the user may have pasted.
SMTP_PASS = "".join(os.environ.get("SMTP_PASS", "").split())
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or NOTIFY_TO)


def _build_message(subject, body, reply_to):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = NOTIFY_TO
    # Yahoo rejects a Reply-To that isn't the authenticated mailbox
    # ("550 No MIME Reply-To header matches auth mailboxes"), so we do NOT set
    # the customer as Reply-To. The customer's email is in the body instead.
    if reply_to and reply_to.strip().lower() == SMTP_FROM.strip().lower():
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    return msg


def _try_send(msg, host, port, mode, context):
    """Send via one method. Raises on failure."""
    if mode == "ssl":
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as s:
            s.ehlo()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    else:  # starttls
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.ehlo()
            s.starttls(context=context)
            s.ehlo()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)


def send_notification(subject, body, reply_to=None):
    """Email NOTIFY_TO, trying SSL(465) then STARTTLS(587). Returns
    (sent, note). Never raises — a mail failure must not break submission."""
    if not SMTP_USER or not SMTP_PASS:
        return False, "email skipped (SMTP_USER/SMTP_PASS not set)"

    context = ssl.create_default_context()
    # 587/STARTTLS negotiates cleanly on most networks, so try it first.
    methods = [(SMTP_HOST, 587, "starttls"), (SMTP_HOST, 465, "ssl")]
    if SMTP_PORT == 465:
        methods.reverse()

    failures = []
    for host, port, mode in methods:
        try:
            _try_send(_build_message(subject, body, reply_to), host, port, mode, context)
            sys.stderr.write(f"[email] sent to {NOTIFY_TO} via {host}:{port} ({mode})\n")
            return True, f"emailed to {NOTIFY_TO} via {port}"
        except Exception as exc:  # noqa: BLE001 - try next method, never crash
            failures.append(f"{port}/{mode}: {type(exc).__name__}: {exc}")

    note = " | ".join(failures)
    sys.stderr.write(f"[email] all methods failed -> {note}\n")
    return False, f"email failed: {note}"

# Photography packages shown on pricing.html (table + order dropdown).
PRODUCTS = [
    {"id": 1, "name": "Mini Session (30 min)", "quality": "Standard", "price": 750},
    {"id": 2, "name": "Portrait Session", "quality": "Standard", "price": 1500},
    {"id": 3, "name": "Graduation / High School Portrait", "quality": "Standard", "price": 3000},
    {"id": 4, "name": "Birthday & Party Coverage", "quality": "Standard and 3D", "price": 3800},
    {"id": 5, "name": "Build Your Own / Event Pictures", "quality": "Standard and 3D", "price": 4500},
    {"id": 6, "name": "Family / Wedding Portrait", "quality": "Quality 3D", "price": 5300},
    {"id": 7, "name": "Premium Full-Day Wedding", "quality": "Premium 3D", "price": 7500},
]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute(
        """CREATE TABLE IF NOT EXISTS contacts (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               fname TEXT, email TEXT, phone TEXT, comments TEXT,
               created_at TEXT
           )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS orders (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT, email TEXT, package TEXT, price INTEGER,
               created_at TEXT
           )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS reviews (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT, rating INTEGER, comment TEXT,
               created_at TEXT
           )"""
    )
    # Migrations: add newer columns if an older DB predates them.
    ocols = [r[1] for r in con.execute("PRAGMA table_info(orders)").fetchall()]
    if "handled" not in ocols:
        con.execute("ALTER TABLE orders ADD COLUMN handled INTEGER DEFAULT 0")
    if "service_date" not in ocols:
        con.execute("ALTER TABLE orders ADD COLUMN service_date TEXT")
    rcols = [r[1] for r in con.execute("PRAGMA table_info(reviews)").fetchall()]
    if "approved" not in rcols:
        # Reviews are hidden until approved in admin (moderation).
        con.execute("ALTER TABLE reviews ADD COLUMN approved INTEGER DEFAULT 0")
    con.commit()
    seed_demo_if_empty(con)
    con.commit()
    con.close()


def _ago(days, hour=12, minute=0):
    d = datetime.now(timezone.utc) - timedelta(days=days)
    return d.strftime(f"%Y-%m-%d {hour:02d}:{minute:02d} UTC")


def seed_demo_if_empty(con):
    """Populate tasteful demo data the first time the DB is empty, so the live
    portfolio site looks alive. No real personal data is used."""
    have = (con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            + con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0])
    if have:
        return
    def _fut(d):
        return (datetime.now(timezone.utc) + timedelta(days=d)).strftime("%Y-%m-%d")
    orders = [
        ("Mariane Petion", "mariane@example.com", "Family / Wedding Portrait", 5300, _fut(10), 0, _ago(4)),
        ("Adu Freeman", "adu@example.com", "Graduation / High School Portrait", 3000, _fut(26), 0, _ago(12)),
        ("Riley Mosafa", "riley@example.com", "Mini Session (30 min)", 750, _fut(5), 0, _ago(26)),
        ("Wonder Delio", "wonder@example.com", "Build Your Own / Event Pictures", 4500, "", 1, _ago(48)),
        ("Nazan Sculer", "nazan@example.com", "Premium Full-Day Wedding", 7500, _fut(40), 0, _ago(70)),
        ("Sarah Karilyl", "sarah@example.com", "Portrait Session", 1500, _fut(18), 0, _ago(95)),
    ]
    con.executemany(
        "INSERT INTO orders (name,email,package,price,service_date,handled,created_at) VALUES (?,?,?,?,?,?,?)",
        orders)
    contacts = [
        ("Mariane Petion", "mariane@example.com", "555-0142", "Do you cover outdoor engagement shoots?", _ago(2)),
        ("Adu Freeman", "adu@example.com", "555-0177", "Interested in a graduation package for June.", _ago(9)),
        ("Riley Mosafa", "riley@example.com", "555-0163", "What is included in the full-day wedding option?", _ago(21)),
    ]
    con.executemany(
        "INSERT INTO contacts (fname,email,phone,comments,created_at) VALUES (?,?,?,?,?)",
        contacts)
    reviews = [
        ("Mariane Petion", 5, "Steve captured our wedding beautifully - every shot tells a story. Highly recommend!", 1, _ago(5)),
        ("Wonder Delio", 5, "Professional, on time, and the graduation photos were stunning. Thank you!", 1, _ago(14)),
        ("Nazan Sculer", 4, "Great experience and lovely pictures. Will definitely book again.", 1, _ago(24)),
        ("Sarah Karilyl", 5, "Fantastic event coverage. Caught every important moment.", 1, _ago(47)),
    ]
    con.executemany(
        "INSERT INTO reviews (name,rating,comment,approved,created_at) VALUES (?,?,?,?,?)",
        reviews)


def db():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def list_gallery_images():
    """Filenames of gallery photos in images/, sorted, layout assets excluded."""
    img_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
    try:
        names = os.listdir(img_dir)
    except OSError:
        return []
    return sorted(
        n for n in names
        if n.lower().endswith(IMAGE_EXTS) and n not in GALLERY_EXCLUDE
    )


class Handler(SimpleHTTPRequestHandler):
    # Force browsers to always fetch the latest files (no stale cache) so
    # edits show up on a normal refresh.
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    # ---- helpers -------------------------------------------------------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, markup, status=200):
        body = markup.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_csv(self, filename, columns, rows):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        for r in rows:
            writer.writerow([r[c] if c in r.keys() else "" for c in columns])
        body = buf.getvalue().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_level(self):
        """Return 'admin', 'demo', or None based on Basic-Auth credentials."""
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                user, _, pw = decoded.partition(":")
                if user == ADMIN_USER and pw == ADMIN_PASS:
                    return "admin"
                if user == DEMO_USER and pw == DEMO_PASS:
                    return "demo"
            except (ValueError, UnicodeDecodeError):
                pass
        return None

    def _require_admin(self, write=False):
        """Allow viewing for admin OR demo; writes (delete/approve/handle)
        require the real admin. Sends a 401/403 and returns False otherwise."""
        level = self._auth_level()
        if level == "admin" or (level == "demo" and not write):
            return True
        if level == "demo" and write:
            self._send_json({"ok": False,
                             "error": "Read-only demo account cannot make changes."}, 403)
            return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Steve Central Admin"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"401 Unauthorized - log in (admin, or demo/demo to view).")
        return False

    def _rate_ok(self):
        """Simple per-IP rate limit for public form posts (spam guard)."""
        ip = self.client_address[0] if self.client_address else "?"
        now_t = datetime.now().timestamp()
        hits = [t for t in _rate_hits.get(ip, []) if now_t - t < RATE_WINDOW]
        if len(hits) >= RATE_MAX:
            _rate_hits[ip] = hits
            return False
        hits.append(now_t)
        _rate_hits[ip] = hits
        return True

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return {}
        # fall back to url-encoded form data
        parsed = parse_qs(raw.decode("utf-8"))
        return {k: v[0] for k, v in parsed.items()}

    # ---- routing -------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/products":
            return self._send_json({"products": PRODUCTS})
        if path == "/api/gallery":
            return self._send_json({"images": list_gallery_images()})
        if path == "/api/reviews":
            # Public: only approved reviews are shown on the site.
            with db() as con:
                rows = con.execute(
                    "SELECT * FROM reviews WHERE approved=1 ORDER BY id DESC").fetchall()
            return self._send_json({"reviews": [dict(r) for r in rows]})
        if path == "/admin/export/reviews.csv":
            if not self._require_admin():
                return
            with db() as con:
                rows = con.execute("SELECT * FROM reviews ORDER BY id DESC").fetchall()
            return self._send_csv("reviews.csv",
                                  ["id", "name", "rating", "comment", "created_at"], rows)
        if path == "/admin/export/contacts.csv":
            if not self._require_admin():
                return
            with db() as con:
                rows = con.execute("SELECT * FROM contacts ORDER BY id DESC").fetchall()
            return self._send_csv("contacts.csv",
                                  ["id", "fname", "email", "phone", "comments", "created_at"], rows)
        if path == "/admin/export/orders.csv":
            if not self._require_admin():
                return
            with db() as con:
                rows = con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
            return self._send_csv("orders.csv",
                                  ["id", "name", "email", "package", "price", "service_date", "handled", "created_at"], rows)
        if path == "/api/contacts":
            if not self._require_admin():
                return
            with db() as con:
                rows = con.execute("SELECT * FROM contacts ORDER BY id DESC").fetchall()
            return self._send_json({"contacts": [dict(r) for r in rows]})
        if path == "/api/orders":
            if not self._require_admin():
                return
            with db() as con:
                rows = con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
            return self._send_json({"orders": [dict(r) for r in rows]})
        if path == "/admin":
            if not self._require_admin():
                return
            return self._send_html(self.render_admin())
        if path == "/dashboard.html":
            # Static dashboard page is admin-only too, so the login prompt
            # caches credentials for the /api/* data it loads.
            if not self._require_admin():
                return
            return super().do_GET()
        if path == "/dashboard":
            # The dashboard now lives in dashboard.html; forward there.
            self.send_response(302)
            self.send_header("Location", "/dashboard.html")
            self.end_headers()
            return
        # everything else: serve static files from this folder
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read_body()

        # Spam guard: rate-limit public form submissions.
        if path in ("/api/contact", "/api/orders", "/api/reviews") and not self._rate_ok():
            return self._send_json(
                {"ok": False, "error": "Too many submissions. Please wait a minute and try again."}, 429)

        if path == "/api/contact":
            fname = (data.get("fname") or "").strip()
            email = (data.get("email") or "").strip()
            if not fname or not email:
                return self._send_json({"ok": False, "error": "Name and email are required."}, 400)
            phone = (data.get("phone") or "").strip()
            comments = (data.get("comments") or "").strip()
            with db() as con:
                cur = con.execute(
                    "INSERT INTO contacts (fname,email,phone,comments,created_at) VALUES (?,?,?,?,?)",
                    (fname, email, phone, comments, now()),
                )
                con.commit()
            send_notification(
                subject=f"New contact message from {fname}",
                body=(f"You have a new contact message on Steve Central's Images.\n\n"
                      f"Name:    {fname}\n"
                      f"Email:   {email}\n"
                      f"Phone:   {phone}\n"
                      f"Message:\n{comments}\n\n"
                      f"Received: {now()}"),
                reply_to=email,
            )
            return self._send_json({"ok": True, "id": cur.lastrowid,
                                    "message": f"Thank you {fname}, we received your request!"})

        if path == "/api/orders":
            name = (data.get("name") or "").strip()
            email = (data.get("email") or "").strip()
            package = (data.get("package") or "").strip()
            try:
                price = int(data.get("price") or 0)
            except (TypeError, ValueError):
                price = 0
            service_date = (data.get("service_date") or "").strip()
            if not name or not email or not package:
                return self._send_json({"ok": False, "error": "Name, email and package are required."}, 400)
            with db() as con:
                cur = con.execute(
                    "INSERT INTO orders (name,email,package,price,service_date,created_at) VALUES (?,?,?,?,?,?)",
                    (name, email, package, price, service_date, now()),
                )
                con.commit()
            send_notification(
                subject=f"New order from {name}",
                body=(f"You have a new order on Steve Central's Images.\n\n"
                      f"Name:         {name}\n"
                      f"Email:        {email}\n"
                      f"Package:      {package}\n"
                      f"Price:        ${price:,}\n"
                      f"Service date: {service_date or '(not specified)'}\n\n"
                      f"Placed: {now()}"),
                reply_to=email,
            )
            return self._send_json({"ok": True, "id": cur.lastrowid,
                                    "message": f"Order placed! {package} for ${price:,}."})

        if path == "/api/reviews":
            # Public: a visitor leaves a review.
            name = (data.get("name") or "").strip()
            comment = (data.get("comment") or "").strip()
            try:
                rating = int(data.get("rating") or 0)
            except (TypeError, ValueError):
                rating = 0
            if not name or not comment:
                return self._send_json({"ok": False, "error": "Name and review are required."}, 400)
            rating = max(1, min(5, rating)) if rating else 5
            with db() as con:
                # approved=0: held for moderation until approved in admin.
                cur = con.execute(
                    "INSERT INTO reviews (name,rating,comment,approved,created_at) VALUES (?,?,?,0,?)",
                    (name, rating, comment, now()),
                )
                con.commit()
            send_notification(
                subject=f"New {rating}-star review from {name} (pending approval)",
                body=(f"A new review is awaiting approval on Steve Central's Images.\n\n"
                      f"Name:   {name}\n"
                      f"Rating: {rating} / 5\n"
                      f"Review:\n{comment}\n\n"
                      f"Approve it in the Admin page. Received: {now()}"),
            )
            return self._send_json({"ok": True, "id": cur.lastrowid,
                                    "message": f"Thank you {name}! Your review was submitted "
                                               f"and will appear once approved."})

        # ---- admin actions (full admin only; demo is read-only) ----
        if path in ("/api/contacts/delete", "/api/orders/delete", "/api/orders/handled",
                    "/api/reviews/delete", "/api/reviews/approve"):
            if not self._require_admin(write=True):
                return
            try:
                row_id = int(data.get("id") or 0)
            except (TypeError, ValueError):
                row_id = 0
            if not row_id:
                return self._send_json({"ok": False, "error": "Valid id required."}, 400)
            with db() as con:
                if path == "/api/contacts/delete":
                    con.execute("DELETE FROM contacts WHERE id=?", (row_id,))
                elif path == "/api/orders/delete":
                    con.execute("DELETE FROM orders WHERE id=?", (row_id,))
                elif path == "/api/reviews/delete":
                    con.execute("DELETE FROM reviews WHERE id=?", (row_id,))
                elif path == "/api/reviews/approve":
                    approved = 1 if str(data.get("approved")) in ("1", "true", "True") else 0
                    con.execute("UPDATE reviews SET approved=? WHERE id=?", (approved, row_id))
                else:  # toggle handled
                    handled = 1 if str(data.get("handled")) in ("1", "true", "True") else 0
                    con.execute("UPDATE orders SET handled=? WHERE id=?", (handled, row_id))
                con.commit()
            return self._send_json({"ok": True})

        return self._send_json({"ok": False, "error": "Unknown endpoint."}, 404)

    # ---- admin view ----------------------------------------------------
    def render_admin(self):
        with db() as con:
            contacts = con.execute("SELECT * FROM contacts ORDER BY id DESC").fetchall()
            orders = con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
            reviews = con.execute("SELECT * FROM reviews ORDER BY id DESC").fetchall()

        def esc(v):
            return html.escape(str(v if v is not None else ""))

        def stars(n):
            n = n or 0
            return "&#9733;" * n + "&#9734;" * (5 - n)

        c_rows = "".join(
            f"<tr><td>{r['id']}</td><td>{esc(r['fname'])}</td><td>{esc(r['email'])}</td>"
            f"<td>{esc(r['phone'])}</td><td>{esc(r['comments'])}</td><td>{esc(r['created_at'])}</td>"
            f"<td><button class='btn-del' data-kind='contacts' data-id='{r['id']}'>Delete</button></td></tr>"
            for r in contacts
        ) or '<tr><td colspan="7">No messages yet.</td></tr>'

        def order_row(r):
            handled = r["handled"] if "handled" in r.keys() and r["handled"] else 0
            badge = "<span style='color:#3ec46d;font-weight:700'>Handled</span>" if handled \
                else "<span style='color:#e7c757'>Pending</span>"
            toggle = "0" if handled else "1"
            label = "Mark pending" if handled else "Mark handled"
            svc = r["service_date"] if "service_date" in r.keys() else ""
            return (
                f"<tr><td>{r['id']}</td><td>{esc(r['name'])}</td><td>{esc(r['email'])}</td>"
                f"<td>{esc(r['package'])}</td><td>${r['price']:,}</td>"
                f"<td>{esc(svc) or '-'}</td><td>{badge}</td>"
                f"<td>{esc(r['created_at'])}</td>"
                f"<td><button class='btn-handled' data-id='{r['id']}' data-to='{toggle}'>{label}</button> "
                f"<button class='btn-del' data-kind='orders' data-id='{r['id']}'>Delete</button></td></tr>"
            )

        o_rows = "".join(order_row(r) for r in orders) \
            or '<tr><td colspan="9">No orders yet.</td></tr>'

        def review_row(r):
            approved = r["approved"] if "approved" in r.keys() and r["approved"] else 0
            badge = "<span style='color:#3ec46d;font-weight:700'>Approved</span>" if approved \
                else "<span style='color:#e7c757'>Pending</span>"
            to = "0" if approved else "1"
            lbl = "Unapprove" if approved else "Approve"
            return (
                f"<tr><td>{r['id']}</td><td>{esc(r['name'])}</td>"
                f"<td style='color:#e7c757;white-space:nowrap'>{stars(r['rating'])}</td>"
                f"<td>{esc(r['comment'])}</td><td>{badge}</td><td>{esc(r['created_at'])}</td>"
                f"<td><button class='btn-approve' data-id='{r['id']}' data-to='{to}'>{lbl}</button> "
                f"<button class='btn-del' data-kind='reviews' data-id='{r['id']}'>Delete</button></td></tr>"
            )

        rv_rows = "".join(review_row(r) for r in reviews) \
            or '<tr><td colspan="7">No reviews yet.</td></tr>'

        return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin - Steve Central's Images</title>
<link rel="stylesheet" href="styles.css"></head>
<body>
<header><h1>Admin Dashboard</h1></header>
<main>
  <p><a href="index.html">&larr; Back to site</a> &nbsp; <a href="/dashboard">View Dashboard &rarr;</a></p>
  <h2 class="gray-background">Contact Messages ({len(contacts)})</h2>
  <p><a href="/admin/export/contacts.csv">Export contacts to CSV</a></p>
  <table id="table-format">
    <tr><th>#</th><th>Name</th><th>Email</th><th>Phone</th><th>Comments</th><th>Received</th><th>Action</th></tr>
    {c_rows}
  </table>
  <h2 class="gray-background">Orders ({len(orders)})</h2>
  <p><a href="/admin/export/orders.csv">Export orders to CSV</a></p>
  <table id="table-format">
    <tr><th>#</th><th>Name</th><th>Email</th><th>Package</th><th>Price</th><th>Service date</th><th>Status</th><th>Placed</th><th>Action</th></tr>
    {o_rows}
  </table>
  <h2 class="gray-background">Reviews ({len(reviews)})</h2>
  <p><a href="/admin/export/reviews.csv">Export reviews to CSV</a></p>
  <table id="table-format">
    <tr><th>#</th><th>Name</th><th>Rating</th><th>Review</th><th>Status</th><th>Posted</th><th>Action</th></tr>
    {rv_rows}
  </table>
</main>
<footer>
<p>Steve Central's Images &nbsp;|&nbsp; Admin</p>
</footer>
<script>
function post(url, body) {{
  return fetch(url, {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(body) }}).then(function (r) {{ return r.json(); }});
}}
document.addEventListener('click', function (e) {{
  var t = e.target;
  if (t.classList.contains('btn-del')) {{
    if (!confirm('Delete this entry? This cannot be undone.')) return;
    post('/api/' + t.dataset.kind + '/delete', {{ id: t.dataset.id }})
      .then(function (d) {{ if (d.ok) location.reload(); else alert(d.error || 'Failed'); }});
  }}
  if (t.classList.contains('btn-handled')) {{
    post('/api/orders/handled', {{ id: t.dataset.id, handled: t.dataset.to }})
      .then(function (d) {{ if (d.ok) location.reload(); else alert(d.error || 'Failed'); }});
  }}
  if (t.classList.contains('btn-approve')) {{
    post('/api/reviews/approve', {{ id: t.dataset.id, approved: t.dataset.to }})
      .then(function (d) {{ if (d.ok) location.reload(); else alert(d.error || 'Failed'); }});
  }}
}});
</script>
</body></html>"""

    # ---- dashboard view ------------------------------------------------
    def render_dashboard(self, range_key="all"):
        with db() as con:
            contacts = con.execute("SELECT * FROM contacts").fetchall()
            orders = con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()

        def esc(v):
            return html.escape(str(v if v is not None else ""))

        now = datetime.now()

        # ---- date-range filter ----
        if range_key == "year":
            cutoff, range_label = f"{now.year:04d}-01-01", "This year"
        elif range_key in ("7", "30", "90"):
            n = int(range_key)
            cutoff = (now - timedelta(days=n)).strftime("%Y-%m-%d")
            range_label = f"Last {n} days"
        else:
            range_key, cutoff, range_label = "all", None, "All time"

        def in_range(ca):
            return (cutoff is None) or ((ca or "")[:10] >= cutoff)

        f_orders = [o for o in orders if in_range(o["created_at"])]
        f_contacts = [c for c in contacts if in_range(c["created_at"])]

        range_options = [("all", "All time"), ("7", "7 days"),
                         ("30", "30 days"), ("90", "90 days"), ("year", "This year")]
        pills = "".join(
            f"<a href='/dashboard?range={k}' class='pill{' active' if k == range_key else ''}'>{lbl}</a>"
            for k, lbl in range_options
        )

        # ---- core metrics (within the selected range) ----
        total_orders = len(f_orders)
        total_revenue = sum((o["price"] or 0) for o in f_orders)
        handled = sum(1 for o in f_orders if "handled" in o.keys() and o["handled"])
        pending = total_orders - handled
        total_contacts = len(f_contacts)
        avg_order = round(total_revenue / total_orders) if total_orders else 0

        # ---- top customers (within range) ----
        cust = {}
        for o in f_orders:
            key = (o["name"] or "(unknown)", o["email"] or "")
            agg = cust.setdefault(key, {"orders": 0, "spent": 0})
            agg["orders"] += 1
            agg["spent"] += (o["price"] or 0)
        top_customers = sorted(cust.items(), key=lambda kv: kv[1]["spent"], reverse=True)[:6]

        # ---- revenue + count per package (within range) ----
        pkg = {}
        for o in f_orders:
            name = o["package"] or "(unknown)"
            agg = pkg.setdefault(name, {"count": 0, "revenue": 0})
            agg["count"] += 1
            agg["revenue"] += (o["price"] or 0)
        by_pkg = sorted(pkg.items(), key=lambda kv: kv[1]["revenue"], reverse=True)
        max_rev = max((v["revenue"] for _, v in by_pkg), default=0)

        # ---- upcoming bookings (service date today or later) ----
        today = now.strftime("%Y-%m-%d")
        upcoming = sorted(
            (o for o in orders
             if "service_date" in o.keys() and o["service_date"] and o["service_date"] >= today),
            key=lambda o: o["service_date"],
        )[:8]

        # ---- revenue over the last 6 months ----
        rev_by_month = {}
        for o in orders:
            k = (o["created_at"] or "")[:7]
            if k:
                rev_by_month[k] = rev_by_month.get(k, 0) + (o["price"] or 0)
        abbr = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        months = []
        yy, mm = now.year, now.month
        for i in range(5, -1, -1):
            m2, y2 = mm - i, yy
            while m2 <= 0:
                m2 += 12
                y2 -= 1
            months.append((abbr[m2], rev_by_month.get(f"{y2:04d}-{m2:02d}", 0)))
        max_month = max((v for _, v in months), default=0)

        # ---- recent activity (orders + messages in range, newest first) ----
        events = []
        for o in f_orders:
            events.append((o["created_at"] or "", "order",
                           f"New order &mdash; {esc(o['name'])}: {esc(o['package'])} "
                           f"(${(o['price'] or 0):,})"))
        for c in f_contacts:
            events.append((c["created_at"] or "", "msg",
                           f"New message &mdash; {esc(c['fname'])} ({esc(c['email'])})"))
        events.sort(key=lambda e: e[0], reverse=True)
        events = events[:8]

        # ---- icons ----
        def icon(p):
            return (f"<svg viewBox='0 0 24 24' width='22' height='22' fill='none' "
                    f"stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
                    f"stroke-linejoin='round'>{p}</svg>")
        I_BOX = icon("<path d='M21 8l-9-5-9 5 9 5 9-5z'/><path d='M3 8v8l9 5 9-5V8'/><path d='M12 13v8'/>")
        I_CASH = icon("<line x1='12' y1='2' x2='12' y2='22'/>"
                      "<path d='M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6'/>")
        I_CLOCK = icon("<circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 2'/>")
        I_MAIL = icon("<rect x='3' y='5' width='18' height='14' rx='2'/><path d='M3 7l9 6 9-6'/>")
        I_CHART = icon("<path d='M4 20V10'/><path d='M10 20V4'/><path d='M16 20v-7'/><path d='M22 20H2'/>")
        I_CAL = icon("<rect x='3' y='5' width='18' height='16' rx='2'/><path d='M3 9h18M8 3v4M16 3v4'/>")

        # ---- KPI cards (all reflect the selected range) ----
        kpis = [
            (I_BOX, "Orders", f"{total_orders}"),
            (I_CASH, "Revenue", f"${total_revenue:,}"),
            (I_CHART, "Avg Order Value", f"${avg_order:,}"),
            (I_CLOCK, "Pending Orders", f"{pending}"),
            (I_CAL, "Handled Orders", f"{handled}"),
            (I_MAIL, "Contact Messages", f"{total_contacts}"),
        ]
        kpi_html = "".join(
            f"<div class='kpi-card'><span class='kpi-icon'>{ic}</span>"
            f"<div class='kpi-value'>{v}</div><div class='kpi-label'>{label}</div></div>"
            for ic, label, v in kpis
        )

        # ---- top customers list ----
        if top_customers:
            rank = 0
            rows = ""
            for (name, email), v in top_customers:
                rank += 1
                rows += (
                    f"<li><span class='cust-rank'>{rank}</span>"
                    f"<span class='cust-name'>{esc(name)}"
                    f"<span class='cust-email'>{esc(email)}</span></span>"
                    f"<span class='cust-spent'>${v['spent']:,}"
                    f"<span class='cust-orders'>{v['orders']} order(s)</span></span></li>"
                )
            top_html = f"<ul class='top-cust'>{rows}</ul>"
        else:
            top_html = "<p>No customers in this range yet.</p>"

        # ---- monthly revenue chart ----
        month_cols = ""
        for label, val in months:
            pct = int(round((val / max_month) * 100)) if max_month else 0
            short = f"${val/1000:.1f}k" if val >= 1000 else f"${val}"
            month_cols += (
                f"<div class='m-col'><div class='m-barwrap'>"
                f"<div class='m-bar' style='height:{pct}%'></div></div>"
                f"<div class='m-val'>{short}</div><div class='m-label'>{label}</div></div>"
            )
        month_chart = f"<div class='month-chart'>{month_cols}</div>"

        # ---- revenue by package bars ----
        if by_pkg:
            bars = ""
            for name, v in by_pkg:
                pct = int(round((v["revenue"] / max_rev) * 100)) if max_rev else 0
                bars += (
                    f"<div class='bar-row'><div class='bar-label'>{esc(name)}</div>"
                    f"<div class='bar-track'><div class='bar-fill' style='width:{pct}%'></div></div>"
                    f"<div class='bar-value'>${v['revenue']:,} "
                    f"<span class='bar-sub'>({v['count']})</span></div></div>"
                )
        else:
            bars = "<p>No orders yet.</p>"

        # ---- order status ----
        if total_orders:
            handled_pct = int(round((handled / total_orders) * 100))
            pending_pct = 100 - handled_pct
            status_html = (
                f"<div class='status-bar'>"
                f"<div class='status-seg status-handled' style='width:{handled_pct}%'>{handled}</div>"
                f"<div class='status-seg status-pending' style='width:{pending_pct}%'>{pending}</div>"
                f"</div><p class='muted-note'>{handled_pct}% of orders handled "
                f"({handled} handled, {pending} pending).</p>"
            )
        else:
            status_html = "<p>No orders yet.</p>"

        # ---- upcoming bookings ----
        if upcoming:
            up = "".join(
                f"<li><strong>{esc(o['service_date'])}</strong> &mdash; "
                f"{esc(o['name'])} ({esc(o['package'])})</li>"
                for o in upcoming
            )
            upcoming_html = f"<ul class='dash-list'>{up}</ul>"
        else:
            upcoming_html = "<p>No upcoming bookings scheduled.</p>"

        # ---- recent activity ----
        if events:
            acts = "".join(
                f"<li class='act-{kind}'><span class='act-date'>{esc(ts)}</span>{text}</li>"
                for ts, kind, text in events
            )
            activity_html = f"<ul class='activity'>{acts}</ul>"
        else:
            activity_html = "<p>No activity yet.</p>"

        greeting_date = now.strftime("%A, %B %d, %Y")
        updated = now.strftime("%I:%M %p").lstrip("0")

        return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard - Steve Central's Images</title>
<link rel="stylesheet" href="styles.css">
<style>
body{{margin:0;}}
.dash-layout{{display:flex;min-height:100vh;align-items:stretch;}}
/* ---- left sidebar ---- */
.dash-sidebar{{width:235px;flex:none;background:linear-gradient(180deg,var(--ink),var(--ink-2));
  color:#ece7f5;padding:26px 18px;border-right:1px solid rgba(224,192,78,0.3);
  position:sticky;top:0;align-self:flex-start;min-height:100vh;}}
.side-logo{{display:block;width:62px;height:62px;border-radius:50%;object-fit:cover;border:2px solid var(--gold);margin-bottom:12px;}}
.dash-brand{{font-family:var(--font-serif);color:var(--gold);font-size:19px;font-weight:700;line-height:1.3;}}
.dash-sub{{color:#a9b6d4;font-size:11pt;margin:4px 0 16px;}}
.greet{{color:#c3cde4;font-size:10.5pt;line-height:1.55;margin:0 0 20px;}}
.greet b{{color:#fff;}}
.greet span{{color:#a9b6d4;}}
.dash-nav a{{display:block;color:#efe9f5;text-decoration:none;padding:11px 14px;border-radius:8px;
  margin-bottom:6px;font-weight:600;border-left:3px solid transparent;transition:background .2s,color .2s;}}
.dash-nav a:hover{{background:rgba(224,192,78,0.10);color:var(--gold);}}
.dash-nav a.active{{background:rgba(224,192,78,0.16);color:var(--gold);border-left-color:var(--gold);}}
.side-quick{{margin-top:22px;border-top:1px solid rgba(255,255,255,0.12);padding-top:14px;}}
.side-quick a{{display:block;color:#c3cde4;text-decoration:none;font-size:10.5pt;padding:7px 0;}}
.side-quick a:hover{{color:var(--gold);}}
/* ---- content ---- */
.dash-main{{flex:1;min-width:0;padding:34px 40px;color:#eef2fb;}}
.dash-head{{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;}}
.dash-head h1{{font-family:var(--font-serif);color:var(--gold);margin:0 0 12px;font-size:30px;}}
.updated{{color:#a9b6d4;font-size:10pt;}}
.dash-main h2{{font-family:var(--font-serif);color:var(--gold);border-bottom:2px solid rgba(224,192,78,0.4);
  padding-bottom:8px;margin:28px 0 14px;font-size:22px;}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:15px;margin:6px 0 10px;}}
.kpi-card{{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);border-top:4px solid var(--gold);border-radius:12px;padding:16px 18px;}}
.kpi-icon{{color:var(--gold);display:block;margin-bottom:8px;}}
.kpi-icon svg{{width:24px;height:24px;}}
.kpi-value{{font-family:var(--font-serif);font-size:29px;font-weight:700;color:var(--gold);line-height:1.1;}}
.kpi-label{{color:#c3cde4;font-size:10.5pt;margin-top:6px;}}
.month-chart{{display:flex;align-items:flex-end;gap:14px;height:180px;margin:6px 0 4px;padding:0 4px;}}
.m-col{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;}}
.m-barwrap{{flex:1;width:100%;display:flex;align-items:flex-end;justify-content:center;}}
.m-bar{{width:70%;max-width:46px;background:linear-gradient(180deg,var(--gold-soft),var(--gold-deep));border-radius:6px 6px 0 0;min-height:3px;}}
.m-val{{color:var(--gold-soft);font-size:9pt;margin-top:7px;font-weight:600;}}
.m-label{{color:#a9b6d4;font-size:9pt;}}
.dash-cols{{display:grid;grid-template-columns:1fr 1fr;gap:28px;align-items:start;}}
.bar-row{{display:flex;align-items:center;gap:12px;margin:10px 0;}}
.bar-label{{width:210px;flex:none;font-weight:600;color:#ece7f5;}}
.bar-track{{flex:1;background:rgba(255,255,255,0.08);border-radius:6px;height:24px;overflow:hidden;}}
.bar-fill{{height:100%;background:linear-gradient(90deg,var(--gold-deep),var(--gold));border-radius:6px;min-width:2px;}}
.bar-value{{width:160px;flex:none;text-align:right;font-weight:700;color:var(--gold-soft);}}
.bar-sub{{color:#a9b6d4;font-weight:400;font-size:10pt;}}
.status-bar{{display:flex;height:30px;border-radius:8px;overflow:hidden;margin:10px 0 6px;}}
.status-seg{{display:flex;align-items:center;justify-content:center;font-weight:700;min-width:0;overflow:hidden;white-space:nowrap;}}
.status-handled{{background:#3ec46d;color:#0d2b16;}}
.status-pending{{background:#e7c757;color:#241a33;}}
.muted-note{{color:#a9b6d4;}}
.dash-list{{list-style:none;padding:0;margin:0;}}
.dash-list li{{padding:10px 12px;border-left:3px solid var(--gold);background:rgba(255,255,255,0.04);border-radius:4px;margin-bottom:8px;}}
.activity{{list-style:none;padding:0;margin:0;}}
.activity li{{padding:9px 12px;border-left:3px solid var(--gold);background:rgba(255,255,255,0.04);border-radius:4px;margin-bottom:7px;}}
.activity li.act-msg{{border-left-color:var(--purple);}}
.act-date{{display:block;color:#a9b6d4;font-size:8.5pt;margin-bottom:2px;}}
.range-bar{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:2px 0 20px;}}
.range-bar .rlabel{{color:#a9b6d4;font-size:10pt;margin-right:2px;}}
.pill{{display:inline-block;padding:6px 14px;border-radius:20px;text-decoration:none;font-size:10pt;font-weight:600;color:#c3cde4;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);}}
.pill:hover{{color:var(--gold);border-color:rgba(224,192,78,0.5);}}
.pill.active{{background:var(--gold);color:var(--ink);border-color:var(--gold);}}
.top-cust{{list-style:none;padding:0;margin:0;}}
.top-cust li{{display:flex;align-items:center;gap:12px;padding:10px 12px;background:rgba(255,255,255,0.04);border-radius:6px;margin-bottom:7px;}}
.cust-rank{{flex:none;width:26px;height:26px;border-radius:50%;background:var(--gold);color:var(--ink);font-weight:700;display:flex;align-items:center;justify-content:center;font-size:10pt;}}
.cust-name{{flex:1;min-width:0;font-weight:600;color:#ece7f5;display:flex;flex-direction:column;}}
.cust-email{{color:#a9b6d4;font-weight:400;font-size:9pt;}}
.cust-spent{{flex:none;text-align:right;font-weight:700;color:var(--gold-soft);display:flex;flex-direction:column;}}
.cust-orders{{color:#a9b6d4;font-weight:400;font-size:9pt;}}
@media(max-width:800px){{
  .dash-layout{{flex-direction:column;}}
  .dash-sidebar{{width:auto;min-height:auto;position:static;border-right:none;border-bottom:1px solid rgba(224,192,78,0.3);}}
  .dash-nav{{display:flex;flex-wrap:wrap;gap:6px;}}
  .dash-nav a{{margin-bottom:0;}}
  .dash-cols{{grid-template-columns:1fr;}}
  .bar-label{{width:120px;}}.bar-value{{width:auto;}}
}}
</style>
</head>
<body>
<div class="dash-layout">
  <aside class="dash-sidebar">
    <img src="images/logo.jpg" alt="Steve Central's Images" class="side-logo">
    <div class="dash-brand">Steve Central's Images</div>
    <div class="dash-sub">SCI Dashboard</div>
    <div class="greet">Welcome back,<br><b>Steve</b><br><span>{greeting_date}</span></div>
    <nav class="dash-nav">
      <a href="/dashboard" class="active">Dashboard</a>
      <a href="/admin">Admin</a>
      <a href="index.html">&larr; Back to site</a>
    </nav>
    <div class="side-quick">
      <a href="/admin/export/orders.csv">Export Orders (CSV)</a>
      <a href="/admin/export/contacts.csv">Export Messages (CSV)</a>
    </div>
  </aside>

  <div class="dash-main">
    <div class="dash-head">
      <h1>SCI Dashboard</h1>
      <span class="updated">Updated {updated} &middot; auto-refreshes</span>
    </div>

    <div class="range-bar"><span class="rlabel">Showing:</span>{pills}</div>

    <div class="kpi-grid">{kpi_html}</div>

    <h2>Monthly Revenue</h2>
    {month_chart}

    <h2>Revenue by Package</h2>
    {bars}

    <h2>Top Customers</h2>
    {top_html}

    <div class="dash-cols">
      <div>
        <h2>Order Status</h2>
        {status_html}
      </div>
      <div>
        <h2>Upcoming Bookings</h2>
        {upcoming_html}
      </div>
    </div>

    <h2>Recent Activity</h2>
    {activity_html}
  </div>
</div>
<script>setTimeout(function(){{ location.reload(); }}, 60000);</script>
</body></html>"""

    # quieter logging
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    # Port priority: command-line arg, then $PORT (set by hosting services
    # like Render/Railway), then 8000 for local use.
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = int(os.environ.get("PORT", "8000"))
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Steve Central's Images backend running at http://localhost:{port}")
    print(f"  Site:  http://localhost:{port}/index.html")
    print(f"  Admin: http://localhost:{port}/admin  (login: {ADMIN_USER} / {ADMIN_PASS})")
    print(f"  Dashboard: http://localhost:{port}/dashboard")
    if SMTP_USER and SMTP_PASS:
        print(f"  Email: notifications ON -> {NOTIFY_TO} (via {SMTP_HOST})")
    else:
        print(f"  Email: notifications OFF -> would send to {NOTIFY_TO}")
        print("         set SMTP_USER and SMTP_PASS (Yahoo app password) to enable.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
