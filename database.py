import sqlite3
import os
from urllib.parse import urlparse
import config

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "bot_database.db"))

def init_db():
    """Инициализация базы данных и автоматическая миграция колонок"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Создаем таблицу users если не существует
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2. Проверяем наличие колонки is_approved и добавляем при необходимости
    cursor.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'is_approved' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN is_approved INTEGER DEFAULT 0")
        
    # 3. Создаем остальные таблицы
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS access_codes (
            code TEXT PRIMARY KEY,
            created_by INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS download_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT,
            title TEXT,
            platform TEXT,
            file_size_mb REAL,
            quality TEXT,
            downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Автоматически одобряем администраторов из config.ADMIN_IDS
    for admin_id in config.ADMIN_IDS:
        cursor.execute("""
            INSERT INTO users (user_id, username, first_name, is_approved)
            VALUES (?, 'admin', 'Admin', 1)
            ON CONFLICT(user_id) DO UPDATE SET is_approved = 1
        """, (admin_id,))
        
    conn.commit()
    conn.close()

def add_user(user_id: int, username: str, first_name: str) -> bool:
    is_admin = user_id in config.ADMIN_IDS
    default_approved = 1 if is_admin else 0
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT is_approved FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row is None:
        cursor.execute("""
            INSERT INTO users (user_id, username, first_name, is_approved)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, first_name, default_approved))
        conn.commit()
        conn.close()
        return is_admin
    else:
        cursor.execute("""
            UPDATE users SET username = ?, first_name = ? WHERE user_id = ?
        """, (username, first_name, user_id))
        conn.commit()
        approved = bool(row[0]) or is_admin
        conn.close()
        return approved

def is_user_approved(user_id: int) -> bool:
    if user_id in config.ADMIN_IDS:
        return True
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_approved FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row[0]) if row else False

def approve_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_approved = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def reject_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_approved = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def create_access_code(code: str, created_by: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO access_codes (code, created_by, is_active) VALUES (?, ?, 1)", (code.strip().upper(), created_by))
    conn.commit()
    conn.close()

def use_access_code(user_id: int, code: str) -> bool:
    code = code.strip().upper()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM access_codes WHERE code = ?", (code,))
    row = cursor.fetchone()
    if row and row[0] == 1:
        cursor.execute("UPDATE users SET is_approved = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

def detect_platform(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if 'youtube' in domain or 'youtu.be' in domain:
        return 'YouTube'
    elif 'tiktok' in domain:
        return 'TikTok'
    elif 'instagram' in domain:
        return 'Instagram'
    elif 'facebook' in domain or 'fb.watch' in domain or 'fb.com' in domain:
        return 'Facebook'
    elif 'telegram' in domain or 't.me' in domain:
        return 'Telegram'
    else:
        return 'Другой ресурс'

def log_download(user_id: int, url: str, title: str, file_size_mb: float, quality: str):
    platform = detect_platform(url)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO download_history (user_id, url, title, platform, file_size_mb, quality)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, url, title, platform, file_size_mb, quality))
    conn.commit()
    conn.close()

def get_user_stats(user_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(file_size_mb), 0) FROM download_history WHERE user_id = ?", (user_id,))
    total_count, total_mb = cursor.fetchone()
    
    cursor.execute("SELECT platform, COUNT(*) FROM download_history WHERE user_id = ? GROUP BY platform ORDER BY COUNT(*) DESC", (user_id,))
    by_platform = cursor.fetchall()
    
    cursor.execute("SELECT title, quality, file_size_mb, downloaded_at FROM download_history WHERE user_id = ? ORDER BY downloaded_at DESC LIMIT 5", (user_id,))
    recent = cursor.fetchall()
    
    conn.close()
    return {
        'total_count': total_count,
        'total_mb': round(total_mb, 2),
        'by_platform': by_platform,
        'recent': recent
    }

def get_global_stats() -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_approved = 1")
    approved_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*), COALESCE(SUM(file_size_mb), 0) FROM download_history")
    total_downloads, total_mb = cursor.fetchone()
    
    conn.close()
    return {
        'total_users': total_users,
        'approved_users': approved_users,
        'total_downloads': total_downloads,
        'total_mb': round(total_mb, 2)
    }

def get_all_approved_users():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE is_approved = 1")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users
