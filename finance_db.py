import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "svgtracker.db"

def connect():
    return sqlite3.connect(DB_PATH)

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            photo_url TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS finance(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            monthly_income REAL DEFAULT 0,
            month TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS mandatory_expenses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT, category TEXT, amount REAL, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS expenses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT, category TEXT, amount REAL, date TEXT
        );
        CREATE TABLE IF NOT EXISTS debts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            person TEXT, amount REAL, debt_type TEXT, status TEXT, date TEXT
        );
        """)

def get_or_create_user(data):
    with connect() as db:
        row=db.execute('SELECT id FROM users WHERE telegram_id=?',(str(data['id']),)).fetchone()
        now=datetime.now().isoformat()
        if row:
            db.execute('UPDATE users SET username=?, first_name=?, last_name=?, photo_url=? WHERE id=?',
                (data.get('username'),data.get('first_name'),data.get('last_name'),data.get('photo_url'),row[0]))
            return row[0]
        cur=db.execute('INSERT INTO users(telegram_id,username,first_name,last_name,photo_url,created_at) VALUES(?,?,?,?,?,?)',
            (str(data['id']),data.get('username'),data.get('first_name'),data.get('last_name'),data.get('photo_url'),now))
        return cur.lastrowid
