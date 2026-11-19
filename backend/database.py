import sqlite3
import os
import sys
from contextlib import contextmanager
from flask import current_app

backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from config import Config

def get_db_connection():
    try:
        db_path = current_app.config.get("DATABASE_PATH", Config.DATABASE_PATH)
    except RuntimeError:
        db_path = Config.DATABASE_PATH
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

@contextmanager
def db_session():
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.executescript("""
        CREATE TABLE IF NOT EXISTS brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            tagline TEXT,
            description TEXT,
            industry TEXT,
            primary_tone TEXT DEFAULT '',
            voice_traits TEXT, -- JSON array of strings
            target_audience TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS brand_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            statement TEXT NOT NULL,
            key_metric TEXT,
            keywords TEXT,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (brand_id) REFERENCES brands (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS brand_terminology (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER NOT NULL,
            term TEXT NOT NULL,
            replacement TEXT NOT NULL,
            replacement_text TEXT,
            reason TEXT,
            category TEXT DEFAULT 'Terminology',
            severity TEXT DEFAULT 'warning', -- critical, warning, info
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (brand_id) REFERENCES brands (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS brand_visual_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER NOT NULL UNIQUE,
            primary_colors TEXT, -- JSON array of {name, hex}
            secondary_colors TEXT, -- JSON array of {name, hex}
            forbidden_colors TEXT, -- JSON array of {name, hex, reason}
            logo_rules TEXT,
            font_family TEXT,
            contrast_minimum TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (brand_id) REFERENCES brands (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS brand_claim_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER NOT NULL,
            rule_name TEXT NOT NULL,
            pattern_or_trigger TEXT NOT NULL,
            severity TEXT DEFAULT 'critical',
            required_disclaimer TEXT,
            replacement_text TEXT,
            explanation TEXT,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (brand_id) REFERENCES brands (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content_type TEXT DEFAULT 'Ad Copy',
            channel TEXT DEFAULT 'Digital Ads',
            original_text TEXT NOT NULL,
            corrected_text TEXT,
            current_version INTEGER DEFAULT 1,
            status TEXT DEFAULT 'draft', -- draft, pending_review, approved, rejected, changes_requested
            risk_score INTEGER DEFAULT 0, -- 0 to 100 compliance score
            risk_level TEXT DEFAULT 'LOW', -- LOW, MODERATE, HIGH, CRITICAL
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (brand_id) REFERENCES brands (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS content_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER NOT NULL,
            version_number INTEGER NOT NULL,
            text TEXT NOT NULL,
            corrected_text TEXT,
            risk_score INTEGER,
            risk_level TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (content_id) REFERENCES content_items (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER NOT NULL,
            version_number INTEGER NOT NULL,
            score INTEGER NOT NULL,
            risk_level TEXT NOT NULL,
            sub_scores TEXT NOT NULL, -- JSON
            engine_used TEXT NOT NULL, -- 'AI_GEMINI', 'AI_OPENAI', 'RULE_BASED_FALLBACK'
            issues TEXT NOT NULL, -- JSON array of issue objects
            summary TEXT,
            corrected_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (content_id) REFERENCES content_items (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER NOT NULL,
            content_version INTEGER,
            reviewer_name TEXT NOT NULL,
            reviewer_role TEXT,
            decision TEXT NOT NULL, -- approved, rejected, changes_requested
            comments TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (content_id) REFERENCES content_items (id) ON DELETE CASCADE
        );
        """)
        for table_name, column_name in (
            ("brand_terminology", "replacement_text"),
            ("brand_claim_rules", "replacement_text"),
            ("reviews", "content_version"),
        ):
            columns = {
                row["name"]
                for row in cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            if column_name not in columns:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} TEXT")

        cursor.execute("""
            UPDATE brand_terminology
            SET replacement_text = trim(replacement)
            WHERE replacement_text IS NULL
              AND instr(replacement, '/') = 0
              AND trim(replacement) != ''
        """)

def dict_from_row(row):
    if row is None:
        return None
    return dict(row)
