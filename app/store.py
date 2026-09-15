import contextlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from app import config

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pools(id INTEGER PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL,zone TEXT NOT NULL,description TEXT NOT NULL,cap INTEGER NOT NULL,deposit INTEGER NOT NULL,target INTEGER NOT NULL,capacity INTEGER NOT NULL,min_qty INTEGER NOT NULL,status TEXT NOT NULL,deadline INTEGER NOT NULL,terms_version TEXT NOT NULL,winner_id INTEGER,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS participations(id INTEGER PRIMARY KEY,pool_id INTEGER NOT NULL REFERENCES pools(id),user_id INTEGER NOT NULL REFERENCES users(id),status TEXT NOT NULL,terms_version TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(pool_id,user_id));
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY,participation_id INTEGER NOT NULL REFERENCES participations(id),purpose TEXT NOT NULL,amount INTEGER NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(participation_id,purpose));
CREATE TABLE IF NOT EXISTS refunds(id INTEGER PRIMARY KEY,payment_id INTEGER NOT NULL UNIQUE REFERENCES payments(id),amount INTEGER NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS offers(id INTEGER PRIMARY KEY,pool_id INTEGER NOT NULL REFERENCES pools(id),supplier TEXT NOT NULL,price INTEGER NOT NULL,min_qty INTEGER NOT NULL,max_qty INTEGER NOT NULL,warranty TEXT NOT NULL,delivery TEXT NOT NULL,valid_until INTEGER NOT NULL,created_at INTEGER NOT NULL,UNIQUE(pool_id,supplier));
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,participation_id INTEGER UNIQUE NOT NULL REFERENCES participations(id),offer_id INTEGER NOT NULL REFERENCES offers(id),amount INTEGER NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY,order_id INTEGER NOT NULL REFERENCES orders(id),user_id INTEGER NOT NULL REFERENCES users(id),message TEXT NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS waitlist(id INTEGER PRIMARY KEY,pool_id INTEGER NOT NULL REFERENCES pools(id),user_id INTEGER NOT NULL REFERENCES users(id),created_at INTEGER NOT NULL,UNIQUE(pool_id,user_id));
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),message TEXT NOT NULL,created_at INTEGER NOT NULL,sent_at INTEGER,attempts INTEGER NOT NULL DEFAULT 0,next_attempt INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor INTEGER,action TEXT NOT NULL,details TEXT NOT NULL,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS requests(user_id INTEGER NOT NULL,key TEXT NOT NULL,payload TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(user_id,key));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''

def now():
    return int(time.time())

@contextlib.contextmanager
def connect(write=False):
    db = sqlite3.connect(config.DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        if write:
            db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def notify(db, uid, message):
    db.execute('INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)', (uid, message, now()))

def audit(db, actor, action, details):
    db.execute('INSERT INTO audit(actor,action,details,created_at) VALUES(?,?,?,?)', (actor, action, json.dumps(details, ensure_ascii=False), now()))

def init():
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
        existing = db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()
        if existing and existing['value'] != config.MODE:
            raise RuntimeError('Use a separate DATABASE_PATH when changing APP_MODE. Demo data must not enter Telegram mode.')
        db.execute("INSERT OR IGNORE INTO settings VALUES('mode',?)", (config.MODE,))
        if config.MODE == 'demo' and not db.execute('SELECT 1 FROM pools').fetchone():
            rows = [
                (1, 'Прохлада для всего дома', 'climate', 'Ташкент · пилотный ЖК', 'Демонстрационный кондиционер · 12 000 BTU/ч · инвертор. В пакет включены доставка в пилотный ЖК, стандартный монтаж с трассой до 3 м и гарантия 2 года. Конкретный SKU и поставщик для реальной закупки еще не выбраны.', 450000000, 20000000, 20, 25, 16),
                (2, 'Чистая вода каждый день', 'water', 'Ташкент · пилотный ЖК', 'Демонстрационный фильтр обратного осмоса. Доставка и стандартная установка включены. Это пример будущей группы, не реальное предложение.', 180000000, 10000000, 15, 20, 12),
                (3, 'Больше уюта вместе', 'home', 'Ташкент · пилотный ЖК', 'Демонстрационный очиститель воздуха. Доставка включена. Это пример будущей группы, не реальное предложение.', 240000000, 10000000, 15, 20, 12),
            ]
            for row in rows:
                db.execute('INSERT INTO pools VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (*row, 'COLLECTING', now()+7*86400, 'pilot-v1', None, now()))
            # Clearly labelled demo fixtures, never created in Telegram mode.
            for uid in range(1001, 1020):
                db.execute('INSERT INTO users VALUES(?,?,?)', (uid, 'Тестовый участник', now()))
                p = db.execute('INSERT INTO participations(pool_id,user_id,status,terms_version,created_at) VALUES(1,?,?,?,?)', (uid, 'RESERVED', 'pilot-v1', now())).lastrowid
                db.execute('INSERT INTO payments(participation_id,purpose,amount,status,created_at) VALUES(?,?,?,?,?)', (p, 'DEPOSIT', 20000000, 'SUCCEEDED', now()))

def session_for(user):
    token = secrets.token_urlsafe(32)
    with connect(True) as db:
        db.execute('INSERT INTO users VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name', (user['id'], user.get('first_name', 'Покупатель')[:100], now()))
        db.execute('DELETE FROM sessions WHERE expires_at<?', (now(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (token,user['id'],now()+3600))
    return token
