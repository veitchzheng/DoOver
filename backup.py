"""备份体系：每日全量 zip、滚动保留、手动触发、备份校验与 VACUUM"""
import json
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from config import (APP_VERSION, BACKUP_DIR, BACKUP_KEEP, DATA_DIR, EXPORT_DIR,
                    EXPORT_KEEP, ensure_dirs)
from db import DB_PATH, get_conn, now_str

TABLES = ("items", "photos", "review_log", "star_ledger")


def do_backup(reason: str = "manual") -> dict:
    """全量备份：db + photos + thumbs + 密钥 + manifest → 单个 zip"""
    ensure_dirs()
    conn = get_conn()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # 把 WAL 并入主库，保证 zip 自包含
    counts = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in TABLES}
    conn.close()

    manifest = {"app": "DoOver", "version": APP_VERSION, "reason": reason,
                "created_at": now_str(), "counts": counts}
    name = f"doover-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    path = BACKUP_DIR / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.write(DB_PATH, "doover.db")
        for extra in ("secret.key", "初始访问码.txt"):
            f = DATA_DIR / extra
            if f.exists():
                zf.write(f, extra)
        for sub in ("photos", "thumbs"):
            d = DATA_DIR / sub
            if d.exists():
                for f in sorted(d.iterdir()):
                    if f.is_file():
                        zf.write(f, f"{sub}/{f.name}")
    _prune_backups()
    prune_exports()
    return {"name": name, "size": path.stat().st_size, "manifest": manifest}


def _prune_backups():
    zips = sorted(BACKUP_DIR.glob("doover-backup-*.zip"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for p in zips[BACKUP_KEEP:]:
        p.unlink(missing_ok=True)


def prune_exports():
    """导出 PDF 只保留最近 EXPORT_KEEP 份"""
    ensure_dirs()
    pdfs = sorted(EXPORT_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in pdfs[EXPORT_KEEP:]:
        p.unlink(missing_ok=True)


def latest_backup():
    zips = sorted(BACKUP_DIR.glob("doover-backup-*.zip"),
                 key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        return None
    p = zips[0]
    age_h = (datetime.now().timestamp() - p.stat().st_mtime) / 3600
    return {"name": p.name, "size": p.stat().st_size, "age_hours": round(age_h, 1)}


def backup_status() -> dict:
    ensure_dirs()
    return {"latest": latest_backup(), "count": len(list(BACKUP_DIR.glob("doover-backup-*.zip"))),
            "keep": BACKUP_KEEP, "dir": str(BACKUP_DIR)}


def verify_backup(zip_path, dest: str | None = None) -> dict:
    """解压校验：库完整性 quick_check + 关键表计数与 manifest 核对"""
    tmp = dest or tempfile.mkdtemp(prefix="doover-verify-")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(tmp)
    dbp = Path(tmp) / "doover.db"
    if not dbp.exists():
        if dest is None:
            shutil.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "error": "备份缺少 doover.db"}
    conn = sqlite3.connect(dbp)
    quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    counts = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()[0] for t in TABLES}
    conn.close()
    mp = Path(tmp) / "manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
    if dest is None:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = quick == "ok"
    if manifest and manifest.get("counts"):
        ok = ok and all(manifest["counts"].get(k) == v for k, v in counts.items())
    return {"ok": ok, "quick_check": quick, "counts": counts, "manifest": manifest}


def vacuum():
    """每月维护：WAL 归并 + VACUUM 压缩"""
    conn = get_conn()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("VACUUM")
    conn.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--status" in args:
        print(json.dumps(backup_status(), ensure_ascii=False, indent=2))
    elif "--run" in args:
        print(json.dumps(do_backup("cli"), ensure_ascii=False, indent=2))
    elif "--verify" in args:
        i = args.index("--verify")
        print(json.dumps(verify_backup(args[i + 1]), ensure_ascii=False, indent=2))
    else:
        print("用法: python backup.py --status | --run | --verify <备份zip路径>")
