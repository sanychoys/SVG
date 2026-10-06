
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
            telegram_id TEXT UNIQUE,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS finance(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            monthly_income REAL DEFAULT 0,
            month TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS mandatory_expenses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            category TEXT,
            amount REAL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS expenses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            title TEXT,
            category TEXT,
            amount REAL,
            date TEXT
        );
        CREATE TABLE IF NOT EXISTS debts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            person TEXT,
            amount REAL,
            debt_type TEXT,
            status TEXT,
            date TEXT
        );
        """)

def get_or_create_user(telegram_id):
    with connect() as db:
        row=db.execute("SELECT id FROM users WHERE telegram_id=?",(str(telegram_id),)).fetchone()
        if row: return row[0]
        cur=db.execute(
            "INSERT INTO users(telegram_id,created_at) VALUES(?,?)",
            (str(telegram_id),datetime.now().isoformat())
        )
        return cur.lastrowid
