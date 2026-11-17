"""SQLite helpers and the database schema."""
import os
import sqlite3
from flask import g
import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('student','mentor','hod','gate','admin')),
    name TEXT NOT NULL,
    reg TEXT NOT NULL UNIQUE,          -- register number or staff ID, stored upper case
    dept TEXT,
    phone TEXT,
    dob TEXT NOT NULL,                 -- date of birth YYYY-MM-DD, used with reg to log in
    mentor_id INTEGER REFERENCES users(id),
    hod_id INTEGER REFERENCES users(id),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS passes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id),
    date TEXT NOT NULL,                -- YYYY-MM-DD
    leave_time TEXT NOT NULL,          -- HH:MM
    return_time TEXT NOT NULL,         -- expected return, HH:MM
    destination TEXT NOT NULL,
    reason TEXT NOT NULL,
    mentor_status TEXT NOT NULL DEFAULT 'Pending',   -- Pending / Approved / Rejected
    mentor_remark TEXT DEFAULT '',
    hod_status TEXT NOT NULL DEFAULT 'Pending',      -- Pending / Approved / Rejected / NA
    hod_remark TEXT DEFAULT '',
    risk TEXT,                         -- ML output, advisory
    risk_prob REAL,
    anomaly TEXT,
    exit_time TEXT,                    -- recorded by the guard when the student leaves
    actual_return TEXT,                -- recorded by the guard when the student returns
    late INTEGER,                      -- 1 if actual_return is after return_time
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_passes_student ON passes(student_id);
CREATE INDEX IF NOT EXISTS idx_passes_date ON passes(date);
"""


def connect(path=None):
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid
