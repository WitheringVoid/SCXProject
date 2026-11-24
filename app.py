from flask import Flask
import sqlite3
import pyotp
import qrcode
import io
import base64
import threading
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.secret_key = 'super_simple_secret_key_123'

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_PATH='/',
)

USERS_DB = 'users.db'
QUEUE_DB = 'queue.db'
CHARS_DB = 'characters.db'

users_db_lock = threading.Lock()
users_db_semaphore = threading.Semaphore(1)

queue_db_lock = threading.Lock()
queue_db_semaphore = threading.Semaphore(1)

chars_db_lock = threading.Lock()
chars_db_semaphore = threading.Semaphore(1)


def enable_wal(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('PRAGMA journal_mode=WAL;')
    cur.execute('PRAGMA synchronous=NORMAL;')
    cur.execute('PRAGMA busy_timeout=5000;')
    conn.commit()
    conn.close()


def init_users_db():
    with users_db_semaphore:
        with users_db_lock:
            conn = sqlite3.connect(USERS_DB)
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    login TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    totp_secret TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            ''')
            conn.commit()
            conn.close()


def init_queue_db():
    with queue_db_semaphore:
        with queue_db_lock:
            conn = sqlite3.connect(QUEUE_DB)
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS player_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nickname TEXT NOT NULL,
                    region TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    next_check_at TEXT NOT NULL,
                    UNIQUE(nickname, region)
                )
            ''')
            conn.commit()
            conn.close()


def init_user_players_table():
    with users_db_semaphore:
        with users_db_lock:
            conn = sqlite3.connect(USERS_DB)
            cur = conn.cursor()
            cur.execute('''
                CREATE TABLE IF NOT EXISTS user_players (
                    user_id INTEGER NOT NULL,
                    nickname TEXT NOT NULL,
                    region TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, nickname, region),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            ''')
            conn.commit()
            conn.close()

def init_chars_db():
    with chars_db_semaphore:
        with chars_db_lock:
            conn = sqlite3.connect(CHARS_DB)
            cur = conn.cursor()
            
            cur.execute('''
                CREATE TABLE IF NOT EXISTS players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nickname TEXT NOT NULL,
                    region TEXT NOT NULL,
                    kills INTEGER DEFAULT 0,
                    deaths INTEGER DEFAULT 0,
                    hours_played INTEGER DEFAULT 0,
                    kd REAL DEFAULT 0,
                    last_online TEXT DEFAULT 'не найден',
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    UNIQUE(nickname, region)
                )
            ''')

            cur.execute('''
                CREATE TABLE IF NOT EXISTS player_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nickname TEXT NOT NULL,
                    region TEXT NOT NULL,
                    kills INTEGER,
                    deaths INTEGER,
                    hours_played INTEGER,
                    kd REAL,
                    recorded_at TEXT NOT NULL
                )
            ''')
            cur.execute('''
                CREATE INDEX IF NOT EXISTS idx_history_nick_reg
                ON player_history (nickname, region, recorded_at)
            ''')
            conn.commit()
            conn.close()

def get_player_history(nickname, region, limit=50):
    with chars_db_semaphore:
        with chars_db_lock:
            conn = sqlite3.connect(CHARS_DB)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute('''
                SELECT kills, deaths, hours_played, kd, recorded_at
                FROM player_history
                WHERE nickname = ? AND region = ?
                ORDER BY recorded_at DESC
                LIMIT ?
            ''', (nickname, region, limit))
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]

def get_user(login):
    with users_db_semaphore:
        with users_db_lock:
            conn = sqlite3.connect(USERS_DB)
            cur = conn.cursor()
            cur.execute(
                'SELECT id, login, password_hash, totp_secret, created_at FROM users WHERE login = ?',
                (login,)
            )
            row = cur.fetchone()
            conn.close()
            return row


def create_user(login, password_hash, totp_secret, created_at):
    with users_db_semaphore:
        with users_db_lock:
            conn = sqlite3.connect(USERS_DB)
            cur = conn.cursor()
            cur.execute(
                'INSERT INTO users (login, password_hash, totp_secret, created_at) VALUES (?, ?, ?, ?)',
                (login, password_hash, totp_secret, created_at)
            )
            conn.commit()
            conn.close()


def get_player(nickname, region):
    with chars_db_semaphore:
        with chars_db_lock:
            conn = sqlite3.connect(CHARS_DB)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute('SELECT * FROM players WHERE nickname = ? AND region = ?', (nickname, region))
            row = cur.fetchone()
            conn.close()
            return row


def add_player_to_queue_if_not_exists(nickname, region):
    with queue_db_semaphore:
        with queue_db_lock:
            conn = sqlite3.connect(QUEUE_DB)
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO player_queue (nickname, region, created_at, next_check_at)
                VALUES (?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
                ON CONFLICT(nickname, region) DO UPDATE SET next_check_at = datetime('now', 'localtime')
            ''', (nickname, region))
            conn.commit()
            conn.close()



def make_qr_base64(secret, login):
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=login, issuer_name='SimpleStalcraftApp')
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode('utf-8')


def add_user_player(user_id, nickname, region):
    with users_db_semaphore:
        with users_db_lock:
            conn = sqlite3.connect(USERS_DB)
            cur = conn.cursor()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cur.execute('''
                INSERT OR IGNORE INTO user_players (user_id, nickname, region, added_at)
                VALUES (?, ?, ?, ?)
            ''', (user_id, nickname, region, now))
            conn.commit()
            conn.close()


def get_user_players(user_id):
    with users_db_semaphore:
        with users_db_lock:
            conn = sqlite3.connect(USERS_DB)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                'SELECT nickname, region, added_at FROM user_players WHERE user_id = ? ORDER BY added_at DESC',
                (user_id,)
            )
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]


def remove_user_player(user_id, nickname, region):
    with users_db_semaphore:
        with users_db_lock:
            conn = sqlite3.connect(USERS_DB)
            cur = conn.cursor()
            cur.execute(
                'DELETE FROM user_players WHERE user_id = ? AND nickname = ? AND region = ?',
                (user_id, nickname, region)
            )
            conn.commit()
            conn.close()


def empty_player_block():
    return {
        'nickname': '',
        'region': '',
        'kills': 0,
        'deaths': 0,
        'hours_played': 0,
        'kd': 0,
        'last_online': 'не найден',
        'updated_at': '-'
    }


def is_player_in_queue(nickname, region):
    with queue_db_semaphore:
        with queue_db_lock:
            conn = sqlite3.connect(QUEUE_DB)
            cur = conn.cursor()
            cur.execute(
                'SELECT 1 FROM player_queue WHERE nickname = ? AND region = ? LIMIT 1',
                (nickname, region)
            )
            ok = cur.fetchone() is not None
            conn.close()
            return ok

from routes import *


if __name__ == '__main__':
    init_users_db()
    init_queue_db()
    init_chars_db()
    init_user_players_table()

    enable_wal(USERS_DB)
    enable_wal(QUEUE_DB)
    enable_wal(CHARS_DB)

    app.run(debug=True)