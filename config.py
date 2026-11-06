"""DoOver 错题本 · 全局配置"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DOOVER_DATA", str(BASE_DIR / "data")))
PHOTO_DIR = DATA_DIR / "photos"
THUMB_DIR = DATA_DIR / "thumbs"
BACKUP_DIR = DATA_DIR / "backups"
EXPORT_DIR = DATA_DIR / "exports"
SECRET_FILE = DATA_DIR / "secret.key"
CODE_FILE = DATA_DIR / "初始访问码.txt"
CHILD_CODE_FILE = DATA_DIR / "孩子访问码.txt"

HOST = os.environ.get("DOOVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("DOOVER_PORT", "8200"))

APP_VERSION = "1.0.0"
BACKUP_KEEP = int(os.environ.get("DOOVER_BACKUP_KEEP", "30"))
EXPORT_KEEP = int(os.environ.get("DOOVER_EXPORT_KEEP", "50"))
SESSION_DAYS = 30

# ---- 业务元数据（前后端共用） ----
SUBJECTS = {"math": "数学", "chinese": "语文", "english": "英语", "other": "其他"}
REASONS = {"concept": "概念不会", "careless": "审题马虎", "calc": "计算失误", "clueless": "完全没思路"}
ITEM_TYPES = {"photo": "照片题", "word": "错字词"}
STATUSES = {"reviewing": "复习中", "paused": "已暂停", "mastered": "已消灭"}

# 复习节奏：录入后第 1/3/7/15/30 天各考一次（自创建日算起；
# 若复习拖堂，自动顺延到次日出题，不压缩后面的节奏）
INTERVAL_OFFSETS = [1, 3, 7, 15, 30]
MASTER_STAGE = 5  # 通过第 5 档即"消灭"


def ensure_dirs():
    for d in (DATA_DIR, PHOTO_DIR, THUMB_DIR, BACKUP_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
