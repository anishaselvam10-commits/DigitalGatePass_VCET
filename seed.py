"""Create the admin account, and optionally demo data.

    python seed.py --demo      adds sample staff, students and pass history (login with register number + date of birth)

The admin account is also created automatically when app.py starts.
"""
import random
import sys
from datetime import date, datetime, timedelta

import config
import db
import ml


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def ensure_admin(conn):
    row = conn.execute("SELECT id FROM users WHERE reg=?", (config.ADMIN_REG,)).fetchone()
    if row:
        return False
    conn.execute(
        "INSERT INTO users(role,name,reg,dept,phone,dob,active,created_at) VALUES(?,?,?,?,?,?,1,?)",
        ("admin", "Administrator", config.ADMIN_REG, None, None, config.ADMIN_DEFAULT_DOB, _now()))
    conn.commit()
    return True


def _add_user(conn, role, name, reg, dept, dob, phone="", mentor_id=None, hod_id=None):
    cur = conn.execute(
        "INSERT INTO users(role,name,reg,dept,phone,dob,mentor_id,hod_id,active,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,1,?)",
        (role, name, reg, dept, phone, dob, mentor_id, hod_id, _now()))
    return cur.lastrowid


def seed_demo(conn):
    if conn.execute("SELECT 1 FROM users WHERE reg='H001'").fetchone():
        print("Demo data already present.")
        return
    rng = random.Random(7)
    hod = _add_user(conn, "hod", "Dr. Ramesh Kumar", "H001", "CSE", "1978-07-01", "9000000001")
    mentor = _add_user(conn, "mentor", "Prof. Anitha S", "M001", "CSE", "1985-03-12", "9000000002")
    _add_user(conn, "gate", "Gate Security", "G001", "CSE", "1990-01-15")

    # (register no, name, how often late, usual hours out)
    students = [("21CS001", "Arun Kumar", 0.05, 2.0, "2004-05-10"), ("21CS002", "Divya R", 0.60, 3.0, "2004-08-21"),
                ("21CS003", "Karthik M", 0.20, 1.5, "2004-02-14"), ("21CS004", "Meena S", 0.0, 2.5, "2004-11-03"),
                ("21CS005", "Suresh P", 0.35, 3.5, "2003-12-27"), ("21CS006", "Priya L", 0.10, 2.0, "2004-06-18")]
    ids = {}
    for reg, name, _, _, dob in students:
        ids[reg] = _add_user(conn, "student", name, reg, "CSE", dob, "98" + reg[-3:] * 3 + "0",
                             mentor_id=mentor, hod_id=hod)

    today = date.today()
    places = [("City Hospital", "Medical checkup"), ("Bank", "Education loan documents"),
              ("Erode Bus Stand", "Collect parcel"), ("Home", "Family function"),
              ("Passport Office", "Document verification"), ("Stationery shop", "Project materials")]
    for reg, _, late_rate, usual, _dob in students:
        if reg == "21CS006":
            continue  # keep one student with no history to show "Insufficient History"
        prior_days = sorted(rng.sample(range(3, 70), 8), reverse=True)
        for days_ago in prior_days:
            d = today - timedelta(days=days_ago)
            leave = rng.uniform(9.0, 12.5)
            dur = max(0.5, rng.gauss(usual, 0.4))
            ret = min(leave + dur, ml.to_hour(config.LATEST_RETURN) - 0.25)
            late = 1 if rng.random() < late_rate else 0
            actual = ret + (rng.uniform(0.3, 1.2) if late else -rng.uniform(0, 0.3))
            place = rng.choice(places)
            conn.execute(
                "INSERT INTO passes(student_id,date,leave_time,return_time,destination,reason,mentor_status,"
                "hod_status,exit_time,actual_return,late,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (ids[reg], d.isoformat(), ml.fmt_hour(leave), ml.fmt_hour(ret), place[0], place[1],
                 "Approved", "Approved", ml.fmt_hour(leave), ml.fmt_hour(actual), late, _now()))
    conn.commit()
    print("Demo data added. Logins (register no. + date of birth): H001 1978-07-01 (HOD), M001 1985-03-12 (mentor), G001 1990-01-15 (gate), 21CS001 2004-05-10 and 21CS002 2004-08-21 (students).")


if __name__ == "__main__":
    db.init_db()
    conn = db.connect()
    if ensure_admin(conn):
        print(f"Admin created: {config.ADMIN_REG} / DOB {config.ADMIN_DEFAULT_DOB}")
    if "--demo" in sys.argv:
        seed_demo(conn)
    conn.close()
