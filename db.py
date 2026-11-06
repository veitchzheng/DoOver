"""SQLite 数据层：连接、建表、种子知识点、settings、安装密钥"""
import hashlib
import secrets
import sqlite3
from datetime import datetime

from config import CHILD_CODE_FILE, CODE_FILE, DATA_DIR, SECRET_FILE, ensure_dirs

DB_PATH = DATA_DIR / "doover.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_points (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject TEXT NOT NULL,
  name TEXT NOT NULL,
  builtin INTEGER NOT NULL DEFAULT 0,
  UNIQUE(subject, name)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject TEXT NOT NULL,
  item_type TEXT NOT NULL DEFAULT 'photo' CHECK(item_type IN ('photo','word')),
  kp_id INTEGER REFERENCES knowledge_points(id) ON DELETE SET NULL,
  reason TEXT CHECK(reason IN ('concept','careless','calc','clueless') OR reason IS NULL),
  prompt TEXT,
  wrong_text TEXT,
  correct_text TEXT,
  wrong_answer TEXT,
  solution TEXT,
  stage INTEGER NOT NULL DEFAULT 0,
  due_date TEXT,
  wrong_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'reviewing' CHECK(status IN ('reviewing','paused','mastered')),
  term TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  mastered_at TEXT,
  question_text TEXT,
  ai_points TEXT,
  ai_pitfalls TEXT,
  ai_hints TEXT,
  ai_reason_suggest TEXT,
  ai_kp_suggest TEXT,
  ai_status TEXT CHECK(ai_status IS NULL OR ai_status IN ('pending','done','failed')),
  ai_model TEXT,
  ai_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_due ON items(status, due_date);
CREATE INDEX IF NOT EXISTS idx_items_subject ON items(subject, status);
CREATE INDEX IF NOT EXISTS idx_items_term ON items(term);
CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  orig_path TEXT NOT NULL,
  thumb_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photos_item ON photos(item_id);
CREATE TABLE IF NOT EXISTS review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  result TEXT NOT NULL CHECK(result IN ('pass','hint','fail')),
  judged_by TEXT NOT NULL DEFAULT 'child' CHECK(judged_by IN ('child','parent')),
  pending_verify INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_item ON review_log(item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_log_date ON review_log(created_at);
CREATE TABLE IF NOT EXISTS star_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event TEXT NOT NULL,
  item_id INTEGER,
  stars INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_star_date ON star_ledger(created_at);
CREATE TABLE IF NOT EXISTS variants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  kp_id INTEGER,
  subject TEXT NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  model TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved')),
  used INTEGER NOT NULL DEFAULT 0,
  result TEXT CHECK(result IS NULL OR result IN ('pass','fail')),
  created_at TEXT NOT NULL,
  judged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_variant_item ON variants(item_id);
"""

# 三、四年级常用知识点预置
SEED_KPS = {
    "math": ["两位数乘法", "两位数除法", "周长与面积", "分数的初步认识", "万以内加减法",
             "时分秒与年月日", "应用题", "角与线段", "小数初步", "混合运算"],
    "chinese": ["形近字", "多音字", "近义词反义词", "阅读理解", "课文背诵",
                "句式改写", "标点符号", "关联词", "修辞手法", "看拼音写汉字"],
    "english": ["单词拼写", "时态", "句型转换", "阅读理解", "听力", "语法选择", "书面表达", "自然拼读"],
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_secret() -> str:
    """安装级随机密钥：Cookie 签名与访问码哈希的盐。首次运行自动生成。"""
    ensure_dirs()
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    secret = secrets.token_hex(32)
    SECRET_FILE.write_text(secret)
    SECRET_FILE.chmod(0o600)
    return secret


# 老库升级：items 表新增列（AI 错题分析）。CREATE TABLE IF NOT EXISTS 不会给已有表加列，
# 这里逐一比对补齐，老数据全部保留。
ITEM_AI_COLUMNS = {
    "question_text": "TEXT",
    "ai_points": "TEXT",
    "ai_pitfalls": "TEXT",
    "ai_hints": "TEXT",
    "ai_reason_suggest": "TEXT",
    "ai_kp_suggest": "TEXT",
    "ai_status": "TEXT",
    "ai_model": "TEXT",
    "ai_error": "TEXT",
}


def migrate(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    for name, decl in ITEM_AI_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {decl}")


def init_db():
    ensure_dirs()
    conn = get_conn()
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.execute("PRAGMA journal_mode=WAL")
    seed_kps(conn)
    init_settings(conn)
    conn.commit()
    conn.close()


def seed_kps(conn):
    for subject, names in SEED_KPS.items():
        for name in names:
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_points(subject, name, builtin) VALUES(?,?,1)",
                (subject, name))


def init_settings(conn):
    defaults = {
        "daily_limit": "8",
        "verify_enabled": "true",
        "ai_hint_enabled": "true",
        "variant_gate": "true",
        "current_term": "三年级上",
        "reward_rule": "20 颗星 = 周末选一部电影；50 颗星 = 实现一个小愿望",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (k, v))
    # 首次安装：生成 6 位访问码（哈希落库，明文仅写入 data/初始访问码.txt 提示家长）
    if settings_get(conn, "access_code_hash") is None:
        secret = load_secret()
        code = "".join(secrets.choice("0123456789") for _ in range(6))
        conn.execute("INSERT INTO settings(key, value) VALUES(?,?)",
                     ("access_code_hash", hashlib.sha256((secret + code).encode()).hexdigest()))
        CODE_FILE.write_text(
            f"DoOver 错题本 · 初始访问码：{code}\n\n"
            "请用手机/平板/电脑浏览器打开本服务，输入此码登录。\n"
            "登录后可在「我的 → 设置」中修改访问码。\n"
            "本文件仅提示用，改码后可删除。\n", encoding="utf-8")
        CODE_FILE.chmod(0o600)
        print(f"[DoOver] 首次安装已生成访问码，见 {CODE_FILE}  →  {code}")
    # 家长/孩子双端分离：孩子码独立生成，孩子设备用它登录只能进复习界面。
    # 升级兼容：已有安装首次升级时生成孩子码，明文提示文件同目录存放。老访问码即家长码。
    if settings_get(conn, "child_code_hash") is None:
        secret = load_secret()
        code = "".join(secrets.choice("0123456789") for _ in range(6))
        conn.execute("INSERT INTO settings(key, value) VALUES(?,?)",
                     ("child_code_hash", hashlib.sha256((secret + code).encode()).hexdigest()))
        CHILD_CODE_FILE.write_text(
            f"DoOver 错题本 · 孩子访问码：{code}\n\n"
            "孩子设备用此码登录，只能看到复习界面（今日 + 我的星星页），\n"
            "无法录题/删除/改设置；家长管理请在家长设备输入家长码登录。\n"
            "可在「我的 → 设置」修改。本文件仅提示用，改码后可删除。\n", encoding="utf-8")
        CHILD_CODE_FILE.chmod(0o600)
        print(f"[DoOver] 已生成孩子访问码，见 {CHILD_CODE_FILE}  →  {code}")


def settings_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def settings_set(conn, key, value):
    conn.execute("INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
