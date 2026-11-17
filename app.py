"""Digital Gate Pass - Flask application.

Run:  python app.py      then open http://127.0.0.1:5000
"""
import qrcode
import csv
import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
import time 
from datetime import date, datetime
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from markupsafe import Markup
from reportlab.graphics import renderPDF, renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

import config
import db
import ml
import seed

# --------------------------------------------------------------------------- setup
def _load_secret():
    os.makedirs(config.INSTANCE_DIR, exist_ok=True)
    path = os.path.join(config.INSTANCE_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(path) as fh:
        return fh.read().strip()


SECRET = _load_secret()
app = Flask(__name__)
app.secret_key = SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
app.teardown_appcontext(db.close_db)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
REG_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-/_.]{2,24}$")
FAILS = {}  # register number -> list of failed login times


def today_str():
    return date.today().isoformat()


def now_hm():
    return datetime.now().strftime("%H:%M")


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- helpers
def overall(p):
    """One word for the whole approval chain."""
    if ml.is_rejected(p):
        return "Rejected"
    if ml.is_approved(p):
        return "Approved"
    return "Awaiting mentor" if p["mentor_status"] == "Pending" else "Awaiting HOD"


def badge_class(text):
    t = text or ""
    if any(w in t for w in ("Approved", "Low", "Normal", "On time", "Returned")) and "late" not in t.lower():
        return "ok"
    if any(w in t for w in ("Rejected", "High", "Unusual", "Late", "Overdue", "late", "Invalid")):
        return "er"
    if t in ("NA", "Not required", "Insufficient History", "Not available"):
        return "na"
    return "wa"


def pass_code(p, student):
    msg = f"{p['id']}|{student['reg']}|{p['date']}|{p['leave_time']}-{p['return_time']}"
    return hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()[:10].upper()


def pass_id(p):
    return f"GP-{p['id']:05d}"


def pass_payload(p, student):
    """Old long format, still accepted by the Verify box."""
    return "|".join([pass_id(p), student["reg"], p["date"], pass_code(p, student)])


def pass_token(p, student):
    return f"{pass_id(p)}-{pass_code(p, student)}"


def base_url():
    return (config.PUBLIC_URL or request.host_url).rstrip("/")


def qr_text(p, student):
    """What the QR holds: a short link, so any phone camera opens the verify page."""
    return f"{base_url()}/g/{pass_token(p, student)}"


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.context_processor
def inject():
    return {
        "college": config.COLLEGE_NAME, "place": config.COLLEGE_PLACE, "cfg": config,
        "user": g.get("user"), "overall": overall, "badge": badge_class,
        "csrf": lambda: Markup(f'<input type="hidden" name="csrf" value="{csrf_token()}">'),
        "pass_id": pass_id,
    }


@app.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        u = db.query("SELECT * FROM users WHERE id=? AND active=1", (uid,), one=True)
        if u:
            g.user = u
        else:
            session.clear()


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400)


def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not g.user:
                return redirect(url_for("login", next=request.path))
            if roles and g.user["role"] not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


def home_for(u):
    return url_for({"student": "student", "mentor": "staff", "hod": "staff",
                    "gate": "gate", "admin": "admin"}[u["role"]])


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
def error_page(e):
    return render_template("error.html", code=e.code, text={
        400: "The form expired or was invalid. Go back, refresh the page and try again.",
        403: "You do not have access to this page.",
        404: "Page not found."}.get(e.code, "Something went wrong.")), e.code


def student_passes(sid):
    return [dict(r) for r in db.query("SELECT * FROM passes WHERE student_id=? ORDER BY id", (sid,))]


# --------------------------------------------------------------------------- auth
def _locked(reg):
    now = time.time()
    FAILS[reg] = [t for t in FAILS.get(reg, []) if now - t < 300]
    return len(FAILS[reg]) >= config.MAX_LOGIN_FAILURES


@app.route("/")
def index():
    return redirect(home_for(g.user) if g.user else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(home_for(g.user))
    if request.method == "POST":
        reg = request.form.get("reg", "").strip().upper()
        dob = request.form.get("dob", "").strip()
        if _locked(reg):
            flash("Too many wrong attempts. Try again in 5 minutes.", "error")
        else:
            u = db.query("SELECT * FROM users WHERE reg=? AND active=1", (reg,), one=True)
            if u and hmac.compare_digest(u["dob"], dob):
                FAILS.pop(reg, None)
                session.clear()
                session["user_id"] = u["id"]
                nxt = request.form.get("next", "")
                if nxt.startswith("/") and not nxt.startswith("//"):
                    return redirect(nxt)
                return redirect(home_for(u))
            FAILS.setdefault(reg, []).append(time.time())
            flash("Wrong register number / staff ID or date of birth.", "error")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def check_user_form(f, role):
    """Validate a new account form (sign-up and admin 'add user'). Returns (errors, clean dict)."""
    errs = []
    name = " ".join(f.get("name", "").split())
    reg = f.get("reg", "").strip().upper()
    dept = f.get("dept", "").strip()
    phone = f.get("phone", "").strip()
    dob = f.get("dob", "").strip()
    if len(name) < 3 or len(name) > 60:
        errs.append("Enter your full name (3 to 60 characters).")
    if not REG_RE.match(reg):
        errs.append("Register number / staff ID must be 3 to 25 letters, digits or - / _ .")
    elif db.query("SELECT 1 FROM users WHERE reg=?", (reg,), one=True):
        errs.append("This register number / staff ID is already registered.")
    if role in ("student", "mentor", "hod") and dept not in config.DEPARTMENTS:
        errs.append("Choose a department.")
    if phone and not re.fullmatch(r"\d{10}", phone):
        errs.append("Phone number must be 10 digits.")
    if role == "student" and not phone:
        errs.append("Phone number is required for students.")
    try:
        d = date.fromisoformat(dob)
        if d >= date.today() or d.year < 1950:
            raise ValueError
    except ValueError:
        errs.append("Enter a valid date of birth.")
    mentor_id = hod_id = None
    if role == "student":
        m = db.query("SELECT id FROM users WHERE id=? AND role='mentor' AND active=1", (f.get("mentor_id"),), one=True)
        h = db.query("SELECT id FROM users WHERE id=? AND role='hod' AND active=1", (f.get("hod_id"),), one=True)
        if not m:
            errs.append("Choose your mentor.")
        if not h:
            errs.append("Choose your HOD.")
        mentor_id, hod_id = (m["id"] if m else None), (h["id"] if h else None)
    return errs, dict(name=name, reg=reg, dept=dept or None, phone=phone or None, dob=dob,
                      mentor_id=mentor_id, hod_id=hod_id)


def insert_user(role, c):
    return db.execute(
        "INSERT INTO users(role,name,reg,dept,phone,dob,mentor_id,hod_id,active,created_at) VALUES(?,?,?,?,?,?,?,?,1,?)",
        (role, c["name"], c["reg"], c["dept"], c["phone"], c["dob"], c["mentor_id"], c["hod_id"], stamp()))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if not config.SELF_SIGNUP_STUDENTS:
        abort(404)
    if g.user:
        return redirect(home_for(g.user))
    if request.method == "POST":
        errs, c = check_user_form(request.form, "student")
        if errs:
            for e in errs:
                flash(e, "error")
        else:
            try:
                insert_user("student", c)
            except sqlite3.IntegrityError:
                flash("This register number is already registered.", "error")
            else:
                flash("Account created. Log in with your register number and date of birth.", "ok")
                return redirect(url_for("login"))
    mentors = db.query("SELECT id,name,dept FROM users WHERE role='mentor' AND active=1 ORDER BY dept,name")
    hods = db.query("SELECT id,name,dept FROM users WHERE role='hod' AND active=1 ORDER BY dept,name")
    return render_template("signup.html", mentors=mentors, hods=hods, form=request.form)


# --------------------------------------------------------------------------- student
def validate_request(f, student):
    errs = []
    d, lv, rt = f.get("date", ""), f.get("leave_time", ""), f.get("return_time", "")
    dest, reason = " ".join(f.get("destination", "").split()), " ".join(f.get("reason", "").split())
    try:
        dd = date.fromisoformat(d)
        if dd < date.today():
            errs.append("Date cannot be in the past.")
    except ValueError:
        errs.append("Enter a valid date.")
        dd = None
    if not (TIME_RE.match(lv) and TIME_RE.match(rt)):
        errs.append("Enter leaving and return times.")
    else:
        if lv >= rt:
            errs.append("Return time must be after leaving time.")
        if lv < config.EARLIEST_LEAVE:
            errs.append(f"Leaving time cannot be before {config.EARLIEST_LEAVE}.")
        if rt > config.LATEST_RETURN:
            errs.append(f"Return time cannot be after {config.LATEST_RETURN}.")
        if lv < rt and ml.hours_between(lv, rt) > config.MAX_HOURS:
            errs.append(f"A pass can be at most {config.MAX_HOURS} hours long.")
        if dd == date.today() and rt <= now_hm():
            errs.append("That return time has already passed today.")
    if len(dest) < 2 or len(dest) > 80:
        errs.append("Enter a destination.")
    if len(reason) < 5 or len(reason) > 200:
        errs.append("Reason must be 5 to 200 characters.")
    if dd:
        n = db.query("SELECT COUNT(*) c FROM passes WHERE student_id=? AND date=? AND mentor_status!='Rejected' "
                     "AND hod_status!='Rejected'", (student["id"], d), one=True)["c"]
        if n >= config.MAX_PASSES_PER_DAY:
            errs.append(f"You already have {config.MAX_PASSES_PER_DAY} passes for {d}.")
    return errs, dict(date=d, leave_time=lv, return_time=rt, destination=dest, reason=reason)


@app.route("/student")
@login_required("student")
def student():
    mine = db.query("SELECT * FROM passes WHERE student_id=? ORDER BY id DESC", (g.user["id"],))
    mentor = db.query("SELECT name FROM users WHERE id=?", (g.user["mentor_id"],), one=True)
    hod = db.query("SELECT name FROM users WHERE id=?", (g.user["hod_id"],), one=True)
    return render_template("student.html", passes=mine, mentor=mentor, hod=hod, today=today_str())


@app.post("/student/apply")
@login_required("student")
def apply():
    errs, c = validate_request(request.form, g.user)
    if errs:
        for e in errs:
            flash(e, "error")
        return redirect(url_for("student"))
    try:
        s = ml.score(student_passes(g.user["id"]), c)
    except Exception as exc:  # the ML layer must never block a request
        app.logger.warning("ML scoring failed: %s", exc)
        s = {"risk": "Not available", "prob": None, "anomaly": "Not available"}
    db.execute(
        "INSERT INTO passes(student_id,date,leave_time,return_time,destination,reason,risk,risk_prob,anomaly,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (g.user["id"], c["date"], c["leave_time"], c["return_time"], c["destination"], c["reason"],
         s["risk"], s["prob"], s["anomaly"], stamp()))
    flash("Request submitted to your mentor.", "ok")
    return redirect(url_for("student"))


# --------------------------------------------------------------------------- mentor / HOD
@app.route("/staff")
@login_required("mentor", "hod")
def staff():
    role = g.user["role"]
    if role == "mentor":
        where, args = "u.mentor_id=?", (g.user["id"],)
    else:
        where, args = "u.hod_id=? AND p.mentor_status='Approved'", (g.user["id"],)
    rows = db.query(f"SELECT p.*, u.name sname, u.reg sreg, u.dept sdept FROM passes p "
                    f"JOIN users u ON u.id=p.student_id WHERE {where} ORDER BY p.id DESC", args)
    hist = {r["student_id"]: r for r in db.query(
        "SELECT student_id, COUNT(*) done, COALESCE(SUM(late),0) lates FROM passes "
        "WHERE actual_return IS NOT NULL GROUP BY student_id")}
    pending = [r for r in rows if (r["mentor_status"] if role == "mentor" else r["hod_status"]) == "Pending"]
    return render_template("staff.html", rows=rows, pending=pending, hist=hist, role=role)


@app.post("/staff/decide/<int:pid>")
@login_required("mentor", "hod")
def decide(pid):
    p = db.query("SELECT * FROM passes WHERE id=?", (pid,), one=True)
    if not p:
        abort(404)
    s = db.query("SELECT * FROM users WHERE id=?", (p["student_id"],), one=True)
    action = request.form.get("action")
    remark = " ".join(request.form.get("remark", "").split())[:200]
    if action not in ("Approved", "Rejected"):
        abort(400)
    if g.user["role"] == "mentor":
        if s["mentor_id"] != g.user["id"] or p["mentor_status"] != "Pending":
            abort(403)
        hod = "NA"
        if action == "Approved":
            short = config.HOD_SKIP_UNDER_HOURS and ml.hours_between(p["leave_time"], p["return_time"]) < config.HOD_SKIP_UNDER_HOURS
            hod = "NA" if short else "Pending"
        db.execute("UPDATE passes SET mentor_status=?, mentor_remark=?, hod_status=? WHERE id=?", (action, remark, hod, pid))
    else:
        if s["hod_id"] != g.user["id"] or p["mentor_status"] != "Approved" or p["hod_status"] != "Pending":
            abort(403)
        db.execute("UPDATE passes SET hod_status=?, hod_remark=? WHERE id=?", (action, remark, pid))
    flash(f"Request {pid} {action.lower()}.", "ok")
    return redirect(url_for("staff"))


# --------------------------------------------------------------------------- pass view, QR, PDF
def _load_pass_for_view(pid):
    p = db.query("SELECT * FROM passes WHERE id=?", (pid,), one=True)
    if not p:
        abort(404)
    s = db.query("SELECT * FROM users WHERE id=?", (p["student_id"],), one=True)
    u = g.user
    allowed = u["role"] in ("admin", "gate") or u["id"] in (s["id"], s["mentor_id"], s["hod_id"])
    if not allowed:
        abort(403)
    if not ml.is_approved(p):
        abort(404)
    return p, s


@app.route("/pass/<int:pid>")
@login_required()
def view_pass(pid):
    p, s = _load_pass_for_view(pid)
    mentor = db.query("SELECT name FROM users WHERE id=?", (s["mentor_id"],), one=True)
    hod = db.query("SELECT name FROM users WHERE id=?", (s["hod_id"],), one=True)
    link = qr_text(p, s)
    local = any(h in link for h in ("127.0.0.1", "localhost"))
    return render_template("pass.html", p=p, s=s, mentor=mentor, hod=hod, code=pass_code(p, s), link=link, local=local)


def qr_drawing(text, size):
    w = QrCodeWidget(text)
    w.barLevel = "M"      # some error correction, helps with phone cameras and printed copies
    b = w.getBounds()
    bw, bh = b[2] - b[0], b[3] - b[1]
    d = Drawing(size, size, transform=[size / bw, 0, 0, size / bh, 0, 0])
    d.add(w)
    return d


@app.route("/pass/<int:pid>/qr.svg")
@login_required()
def pass_qr(pid):
    p, s = _load_pass_for_view(pid)
    svg = renderSVG.drawToString(qr_drawing(qr_text(p, s), 300))
    return Response(svg, mimetype="image/svg+xml")


@app.route("/pass/<int:pid>.pdf")
@login_required()
def pass_pdf(pid):
    p, s = _load_pass_for_view(pid)
    mentor = db.query("SELECT name FROM users WHERE id=?", (s["mentor_id"],), one=True)
    hod = db.query("SELECT name FROM users WHERE id=?", (s["hod_id"],), one=True)
    data = build_pdf(p, s, mentor["name"] if mentor else "Mentor", hod["name"] if hod else "HOD", qr_text(p, s))
    return Response(data, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{pass_id(p)}.pdf"'})


def build_pdf(p, s, mentor_name, hod_name, link):
    navy = HexColor("#1e3a8a")
    green = HexColor("#166534")
    grey = HexColor("#64748b")
    buf = io.BytesIO()
    W, H = A5
    c = canvas.Canvas(buf, pagesize=A5)
    c.setTitle(pass_id(p))
    c.setStrokeColor(navy)
    c.setLineWidth(2.5)
    c.rect(7 * mm, 7 * mm, W - 14 * mm, H - 14 * mm)
    c.setLineWidth(0.6)
    c.rect(9 * mm, 9 * mm, W - 18 * mm, H - 18 * mm)
    y = H - 18 * mm
    c.setFillColor(navy)
    c.setFont("Helvetica-Bold", 13)
    for line in simpleSplit(config.COLLEGE_NAME.upper(), "Helvetica-Bold", 13, W - 30 * mm):
        c.drawCentredString(W / 2, y, line)
        y -= 6 * mm
    c.setFont("Helvetica", 9)
    c.setFillColor(grey)
    c.drawCentredString(W / 2, y, config.COLLEGE_PLACE)
    y -= 9 * mm
    c.setFillColor(navy)
    c.rect(9 * mm, y - 2.5 * mm, W - 18 * mm, 8 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#ffffff"))
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(W / 2, y, "S T U D E N T   G A T E   P A S S")
    y -= 10 * mm
    hod_text = "Not required (short pass)" if p["hod_status"] == "NA" else hod_name
    rows = [("Gate Pass ID", pass_id(p)), ("Student", s["name"]), ("Register No.", s["reg"]),
            ("Department", s["dept"] or "-"), ("Date", p["date"]),
            ("Leaving", p["leave_time"]), ("Expected return", p["return_time"]),
            ("Destination", p["destination"]), ("Reason", p["reason"]),
            ("Mentor approval", mentor_name + (f" - {p['mentor_remark']}" if p["mentor_remark"] else "")),
            ("HOD approval", hod_text + (f" - {p['hod_remark']}" if p["hod_remark"] else ""))]
    x0, x1 = 14 * mm, 52 * mm
    for label, value in rows:
        c.setFont("Helvetica", 8.5)
        c.setFillColor(grey)
        c.drawString(x0, y, label)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(HexColor("#111111"))
        lines = simpleSplit(str(value), "Helvetica-Bold", 9, W - x1 - 14 * mm)[:2]
        for i, line in enumerate(lines):
            c.drawString(x1, y - i * 4 * mm, line)
        y -= 6 * mm + (4 * mm if len(lines) > 1 else 0)
        c.setStrokeColor(HexColor("#cbd5e1"))
        c.setLineWidth(0.3)
        c.line(x0, y + 4 * mm, W - 14 * mm, y + 4 * mm)
    y -= 1 * mm
    c.setStrokeColor(green)
    c.setFillColor(green)
    c.setLineWidth(1.2)
    c.rect(22 * mm, y - 3 * mm, W - 44 * mm, 8 * mm)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(W / 2, y, f"APPROVED - VALID FOR ONE EXIT ON {p['date']}")
    qr_size = 38 * mm
    renderPDF.draw(qr_drawing(link, qr_size), c, (W - qr_size) / 2, y - 6 * mm - qr_size)
    y -= 10 * mm + qr_size
    c.setFont("Helvetica", 8)
    c.setFillColor(grey)
    c.drawCentredString(W / 2, y, f"Gate scan code: {pass_code(p, s)}")
    c.setStrokeColor(HexColor("#111111"))
    c.setLineWidth(0.5)
    for i, label in enumerate(["Student", "Mentor", "HOD"]):
        cx = 14 * mm + i * ((W - 28 * mm) / 3)
        c.line(cx, 21 * mm, cx + 30 * mm, 21 * mm)
        c.drawCentredString(cx + 15 * mm, 17.5 * mm, label)
    c.setFont("Helvetica", 7)
    c.drawCentredString(W / 2, 12 * mm, "Show this pass to security at the gate. Not valid after the expected return time.")
    c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------- gate
def gate_rows():
    return db.query(
        "SELECT p.*, u.name sname, u.reg sreg, u.dept sdept FROM passes p JOIN users u ON u.id=p.student_id "
        "WHERE p.date=? AND p.mentor_status='Approved' AND p.hod_status IN ('Approved','NA') "
        "ORDER BY p.leave_time", (today_str(),))


def gate_state(p, now):
    if p["actual_return"]:
        return "Returned late" if p["late"] else "Returned on time"
    if p["exit_time"]:
        return "Overdue" if now > p["return_time"] else "Out"
    return "Expired, not used" if now > p["return_time"] else "Not left yet"


def render_gate(result=None, scan=""):
    now = now_hm()
    rows = gate_rows()
    states = {r["id"]: gate_state(r, now) for r in rows}
    stats = {
        "approved": len(rows),
        "out": sum(1 for r in rows if states[r["id"]] in ("Out", "Overdue")),
        "returned": sum(1 for r in rows if r["actual_return"]),
        "late": sum(1 for r in rows if r["late"]),
        "overdue": sum(1 for r in rows if states[r["id"]] == "Overdue"),
    }
    q = request.args.get("q", "").strip().lower()
    if q:
        rows = [r for r in rows if q in (pass_id(r) + r["sname"] + r["sreg"]).lower()]
    return render_template("gate.html", rows=rows, states=states, stats=stats, result=result,
                           scan=scan, now=now, q=q)


@app.route("/gate")
@login_required("gate", "admin")
def gate():
    return render_gate()


def verify_text(text):
    """Check scanned text. Accepts the QR link, the bare token (GP-00041-0028FC3C4C) or the old long format."""
    text = text.strip()
    result = {"ok": False, "msg": "Invalid pass. The code could not be read.", "p": None}
    pid = code = reg = pdate = None
    m = re.search(r"(GP-\d{5})-([0-9A-Fa-f]{10})(?![0-9A-Za-z])", text)
    parts = text.split("|")
    if m:
        pid, code = m.group(1), m.group(2)
    elif len(parts) >= 4 and re.fullmatch(r"GP-\d{5}", parts[0]):
        pid, reg, pdate, code = parts[0], parts[1], parts[2], parts[3]
    if not pid:
        return result
    p = db.query("SELECT * FROM passes WHERE id=?", (int(pid[3:]),), one=True)
    if not p or not ml.is_approved(p):
        result["msg"] = "Invalid pass. It was not found or is not fully approved."
        return result
    s = db.query("SELECT * FROM users WHERE id=?", (p["student_id"],), one=True)
    if (not hmac.compare_digest(code.upper(), pass_code(p, s)) or (reg and reg != s["reg"])
            or (pdate and pdate != p["date"])):
        result["msg"] = "Invalid pass. Details do not match the system. It may be fake or edited."
    elif p["date"] != today_str():
        result["msg"] = f"Pass is not valid today. It is valid on {p['date']} only."
    else:
        result = {"ok": True, "p": dict(p), "s": dict(s), "state": gate_state(p, now_hm()), "msg": ""}
    return result


@app.post("/gate/verify")
@login_required("gate", "admin")
def gate_verify():
    text = request.form.get("scan", "").strip()
    return render_gate(verify_text(text), text)


@app.route("/g/<token>")
@login_required("gate", "admin")
def gate_link(token):
    """Opened when a phone camera scans the QR code on a pass."""
    return render_gate(verify_text(token), token)


def _time_or_now(f):
    t = f.get("time", "").strip()
    return t if TIME_RE.match(t) else now_hm()


def _today_approved_pass(pid):
    p = db.query("SELECT * FROM passes WHERE id=?", (pid,), one=True)
    if not p or not ml.is_approved(p) or p["date"] != today_str():
        abort(404)
    return p


@app.post("/gate/exit/<int:pid>")
@login_required("gate", "admin")
def gate_exit(pid):
    p = _today_approved_pass(pid)
    if p["exit_time"]:
        flash("Exit was already recorded for this pass.", "error")
    else:
        db.execute("UPDATE passes SET exit_time=? WHERE id=?", (_time_or_now(request.form), pid))
        flash(f"{pass_id(p)}: exit recorded.", "ok")
    return redirect(url_for("gate"))


@app.post("/gate/return/<int:pid>")
@login_required("gate", "admin")
def gate_return(pid):
    p = _today_approved_pass(pid)
    if not p["exit_time"]:
        flash("Record the exit before the return.", "error")
    elif p["actual_return"]:
        flash("Return was already recorded for this pass.", "error")
    else:
        t = _time_or_now(request.form)
        db.execute("UPDATE passes SET actual_return=?, late=? WHERE id=?", (t, 1 if t > p["return_time"] else 0, pid))
        flash(f"{pass_id(p)}: return recorded ({'late' if t > p['return_time'] else 'on time'}).", "ok")
    return redirect(url_for("gate"))


# --------------------------------------------------------------------------- admin
@app.route("/admin")
@login_required("admin")
def admin():
    role = request.args.get("role", "")
    users = db.query("SELECT * FROM users" + (" WHERE role=?" if role else "") + " ORDER BY role, dept, reg",
                     (role,) if role else ())
    mentors = db.query("SELECT id,name,dept FROM users WHERE role='mentor' AND active=1 ORDER BY dept,name")
    hods = db.query("SELECT id,name,dept FROM users WHERE role='hod' AND active=1 ORDER BY dept,name")
    allp = db.query("SELECT * FROM passes")
    stats = {
        "total": len(allp),
        "approved": sum(1 for p in allp if ml.is_approved(p)),
        "rejected": sum(1 for p in allp if ml.is_rejected(p)),
        "pending": sum(1 for p in allp if not ml.is_approved(p) and not ml.is_rejected(p)),
        "students": db.query("SELECT COUNT(*) c FROM users WHERE role='student'", one=True)["c"],
    }
    by_dept = db.query(
        "SELECT u.dept, COUNT(*) total, COALESCE(SUM(p.late),0) late FROM passes p JOIN users u ON u.id=p.student_id "
        "WHERE p.actual_return IS NOT NULL GROUP BY u.dept ORDER BY late DESC")
    top_late = db.query(
        "SELECT u.name, u.reg, u.dept, COUNT(*) total, SUM(p.late) late FROM passes p JOIN users u ON u.id=p.student_id "
        "WHERE p.actual_return IS NOT NULL GROUP BY u.id HAVING late > 0 ORDER BY late DESC, total DESC LIMIT 5")
    return render_template("admin.html", users=users, mentors=mentors, hods=hods, stats=stats, by_dept=by_dept,
                           top_late=top_late, info=ml.model_info(), role_filter=role, form=request.form)


@app.post("/admin/users/add")
@login_required("admin")
def admin_add():
    role = request.form.get("role", "")
    if role not in ("student", "mentor", "hod", "gate"):
        abort(400)
    errs, c = check_user_form(request.form, role)
    if errs:
        for e in errs:
            flash(e, "error")
    else:
        insert_user(role, c)
        flash(f"Added {c['name']} ({c['reg']}).", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:uid>", methods=["GET", "POST"])
@login_required("admin")
def admin_user(uid):
    u = db.query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    if not u:
        abort(404)
    if request.method == "POST":
        errs = []
        name = " ".join(request.form.get("name", "").split())
        dept = request.form.get("dept", "").strip() or None
        phone = request.form.get("phone", "").strip() or None
        dob = request.form.get("dob", "").strip()
        active = 1 if request.form.get("active") else 0
        mentor_id, hod_id = u["mentor_id"], u["hod_id"]
        if len(name) < 3:
            errs.append("Enter a name.")
        if dept and dept not in config.DEPARTMENTS:
            errs.append("Unknown department.")
        if phone and not re.fullmatch(r"\d{10}", phone):
            errs.append("Phone number must be 10 digits.")
        try:
            date.fromisoformat(dob)
        except ValueError:
            errs.append("Enter a valid date of birth.")
        if u["id"] == g.user["id"] and not active:
            errs.append("You cannot deactivate your own account.")
        if u["role"] == "student":
            m = db.query("SELECT id FROM users WHERE id=? AND role='mentor'", (request.form.get("mentor_id"),), one=True)
            h = db.query("SELECT id FROM users WHERE id=? AND role='hod'", (request.form.get("hod_id"),), one=True)
            if not (m and h):
                errs.append("Choose a mentor and an HOD.")
            else:
                mentor_id, hod_id = m["id"], h["id"]
        if errs:
            for e in errs:
                flash(e, "error")
        else:
            db.execute("UPDATE users SET name=?, dept=?, phone=?, dob=?, active=?, mentor_id=?, hod_id=? WHERE id=?",
                       (name, dept, phone, dob, active, mentor_id, hod_id, uid))
            flash("Saved.", "ok")
            return redirect(url_for("admin"))
    mentors = db.query("SELECT id,name,dept FROM users WHERE role='mentor' ORDER BY dept,name")
    hods = db.query("SELECT id,name,dept FROM users WHERE role='hod' ORDER BY dept,name")
    return render_template("admin_user.html", u=u, mentors=mentors, hods=hods)


@app.post("/admin/retrain")
@login_required("admin")
def admin_retrain():
    by_student = {}
    for r in db.query("SELECT * FROM passes ORDER BY id"):
        by_student.setdefault(r["student_id"], []).append(dict(r))
    meta = ml.train(ml.rows_from_passes(by_student))
    flash(f"Models retrained with {meta['real_rows']} real and {meta['synthetic_rows']} synthetic rows. "
          f"Hold-out AUC {meta['holdout_auc']}.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/export.csv")
@login_required("admin")
def admin_export():
    rows = db.query("SELECT p.*, u.reg, u.name, u.dept FROM passes p JOIN users u ON u.id=p.student_id ORDER BY p.id")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["pass_id", "register_no", "student", "dept", "date", "leave", "expected_return", "destination",
                "reason", "status", "mentor", "mentor_remark", "hod", "hod_remark", "ml_risk", "ml_late_prob",
                "ml_anomaly", "exit_time", "actual_return", "late"])
    for r in rows:
        w.writerow([pass_id(r), r["reg"], r["name"], r["dept"], r["date"], r["leave_time"], r["return_time"],
                    r["destination"], r["reason"], overall(r), r["mentor_status"], r["mentor_remark"],
                    r["hod_status"], r["hod_remark"], r["risk"], r["risk_prob"], r["anomaly"],
                    r["exit_time"], r["actual_return"], r["late"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=gate_passes.csv"})


# --------------------------------------------------------------------------- start
def bootstrap():
    db.init_db()
    conn = db.connect()
    if seed.ensure_admin(conn):
        print(f"Admin account created: {config.ADMIN_REG} / date of birth {config.ADMIN_DEFAULT_DOB} (change it after logging in)")
    conn.close()
    ml.load_models()  # trains on first run, takes a few seconds


bootstrap()

if __name__ == "__main__":
    bootstrap()
    app.run(host="0.0.0.0", port=5000, debug=False)