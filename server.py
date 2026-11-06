"""DoOver 错题本 · FastAPI 服务端入口

本机运行：.venv/bin/python server.py   （默认 http://0.0.0.0:8200）
容器运行：python server.py
"""
import hmac
import io
import json
import secrets
import shutil
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

import auth
import backup
import db
import engine
import pdfgen
from config import (APP_VERSION, BACKUP_DIR, BASE_DIR, DATA_DIR, EXPORT_DIR, ITEM_TYPES,
                    PHOTO_DIR, PORT, HOST, REASONS, SESSION_DAYS, STATUSES, SUBJECTS,
                    THUMB_DIR, ensure_dirs)

MAX_UPLOAD = 20 * 1024 * 1024  # 20MB
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _backup_job():
    try:
        backup.do_backup("auto")
    except Exception:
        traceback.print_exc()


def _vacuum_job():
    try:
        backup.vacuum()
    except Exception:
        traceback.print_exc()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler.add_job(_backup_job, "cron", hour=3, minute=30, id="daily_backup",
                       replace_existing=True)
    scheduler.add_job(_vacuum_job, "cron", day=1, hour=4, minute=10, id="monthly_vacuum",
                      replace_existing=True)
    if not scheduler.running:  # 防嵌套生命周期重复启动（测试场景）
        scheduler.start()
    yield
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        pass


app = FastAPI(title="DoOver 错题本", version=APP_VERSION, lifespan=lifespan)


def require_auth(request: Request):
    tok = request.cookies.get(auth.COOKIE_NAME)
    if not tok or not auth.check_token(tok):
        raise HTTPException(status_code=401, detail="未登录或会话已过期")


public = APIRouter(prefix="/api")                       # 登录/健康检查（免鉴权）
api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])  # 业务 API
photos_api = APIRouter(dependencies=[Depends(require_auth)])           # 图片服务


# ---------- 公共接口 ----------

@public.post("/login")
async def login(request: Request, body: dict = Body(...)):
    ip = request.client.host if request.client else "unknown"
    if auth.is_locked(ip):
        raise HTTPException(429, "尝试次数过多，请 10 分钟后再试")
    code = str(body.get("code", "")).strip()
    conn = db.get_conn()
    expected = db.settings_get(conn, "access_code_hash") or ""
    conn.close()
    if not code or not hmac.compare_digest(auth.hash_code(code), expected):
        auth.record_fail(ip)
        raise HTTPException(401, "访问码不正确")
    auth.clear_fails(ip)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE_NAME, auth.make_token(), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", path="/")
    return resp


@public.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


@public.get("/health")
def health():
    """自检脚本与统计页角标共用；不做鉴权，但只暴露健康度，不含业务数据"""
    conn = db.get_conn()
    quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    n_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    conn.close()
    free_mb = shutil.disk_usage(DATA_DIR).free // (1024 * 1024)
    return {"status": "ok", "version": APP_VERSION, "db": quick, "items": n_items,
            "disk_free_mb": free_mb, "backup": backup.backup_status()}


# ---------- 序列化 ----------

def _row(conn, r, with_photos=True) -> dict:
    d = dict(r)
    d["stubborn"] = engine.is_stubborn(r)
    d["kp_name"] = None
    if d["kp_id"]:
        k = conn.execute("SELECT name FROM knowledge_points WHERE id=?", (d["kp_id"],)).fetchone()
        d["kp_name"] = k["name"] if k else None
    if with_photos:
        d["photo_ids"] = [x["id"] for x in conn.execute(
            "SELECT id FROM photos WHERE item_id=? ORDER BY id", (d["id"],))]
    d["subject_label"] = SUBJECTS.get(d["subject"], d["subject"])
    d["reason_label"] = REASONS.get(d["reason"]) if d["reason"] else None
    d["type_label"] = ITEM_TYPES.get(d["item_type"], d["item_type"])
    return d


def _clip(s, n) -> Optional[str]:
    if s is None:
        return None
    s = str(s).strip()
    return s[:n] if s else None


# ---------- 元数据 ----------

@api.get("/meta")
def meta():
    conn = db.get_conn()
    kps = [dict(r) for r in conn.execute(
        "SELECT id, subject, name FROM knowledge_points ORDER BY subject, builtin DESC, id")]
    s = {k: db.settings_get(conn, k) for k in
         ("daily_limit", "verify_enabled", "current_term", "reward_rule")}
    conn.close()
    return {
        "subjects": SUBJECTS, "reasons": REASONS, "statuses": STATUSES,
        "item_types": ITEM_TYPES, "kps": kps,
        "settings": {
            "daily_limit": int(s["daily_limit"] or 8),
            "verify_enabled": s["verify_enabled"] == "true",
            "current_term": s["current_term"],
            "reward_rule": s["reward_rule"],
        },
        "version": APP_VERSION,
    }


# ---------- 今日 ----------

@api.get("/today")
def today():
    conn = db.get_conn()
    try:
        limit = int(db.settings_get(conn, "daily_limit", "8") or 8)
        t_iso = engine.today().isoformat()
        rows = engine.get_today_list(conn, limit, t_iso)
        items = [_row(conn, r) for r in rows]
        remaining = engine.remaining_due(conn, t_iso)
        done = conn.execute("SELECT COUNT(*) c FROM review_log WHERE substr(created_at,1,10)=?",
                            (t_iso,)).fetchone()["c"]
        stars_today = conn.execute(
            "SELECT COALESCE(SUM(stars),0) s FROM star_ledger WHERE substr(created_at,1,10)=?",
            (t_iso,)).fetchone()["s"]
        pending = conn.execute("SELECT COUNT(*) c FROM review_log WHERE pending_verify=1"
                               ).fetchone()["c"]
        return {"items": items, "limit": limit, "total_due": remaining,
                "done_today": done, "stars_today": stars_today,
                "pending_verify": pending, "streak": engine.streak(conn, t_iso)}
    finally:
        conn.close()


# ---------- 错题 CRUD ----------

def _insert_item(conn, data: dict, item_type: str, term: str, now: str) -> int:
    subject = data.get("subject")
    if subject not in SUBJECTS:
        raise HTTPException(400, "科目不合法")
    reason = data.get("reason")
    if reason is not None and reason not in REASONS:
        raise HTTPException(400, "错因不合法")
    kp_id = data.get("kp_id")
    if kp_id:
        if not conn.execute("SELECT 1 FROM knowledge_points WHERE id=?", (kp_id,)).fetchone():
            raise HTTPException(400, "知识点不存在")
    else:
        kp_name = _clip(data.get("kp_name"), 30)
        if kp_name:  # 支持自定义知识点：有则用，无则建
            row = conn.execute("SELECT id FROM knowledge_points WHERE subject=? AND name=?",
                               (subject, kp_name)).fetchone()
            kp_id = (row["id"] if row else conn.execute(
                "INSERT INTO knowledge_points(subject, name, builtin) VALUES(?,?,0)",
                (subject, kp_name)).lastrowid)
        else:
            kp_id = None
    cur = conn.execute(
        "INSERT INTO items(subject, item_type, kp_id, reason, prompt, wrong_text, correct_text,"
        " wrong_answer, solution, stage, due_date, wrong_count, status, term, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,0,?,0,'reviewing',?,?)",
        (subject, item_type, kp_id, reason,
         _clip(data.get("prompt"), 200), _clip(data.get("wrong_text"), 200),
         _clip(data.get("correct_text"), 200), _clip(data.get("wrong_answer"), 500),
         _clip(data.get("solution"), 1000),
         engine.due_after_create(engine.today()), term, now))
    return cur.lastrowid


def _save_photo(conn, data: bytes, item_id: int, now: str):
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "照片超过 20MB")
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise HTTPException(400, "无法解析图片文件")
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((1600, 1600))  # 服务端兜底压缩（客户端已压至长边 1200）
    stem = f"{item_id}_{secrets.token_hex(4)}"
    orig = PHOTO_DIR / f"{stem}.jpg"
    im.save(orig, "JPEG", quality=85)
    t = im.copy()
    t.thumbnail((360, 360))
    thumb = THUMB_DIR / f"{stem}.jpg"
    t.save(thumb, "JPEG", quality=75)
    conn.execute("INSERT INTO photos(item_id, orig_path, thumb_path, created_at) VALUES(?,?,?,?)",
                 (item_id, str(orig), str(thumb), now))


@api.post("/items")
async def create_item(request: Request):
    ctype = request.headers.get("content-type", "")
    conn = db.get_conn()
    try:
        now = db.now_str()
        term = db.settings_get(conn, "current_term")
        if ctype.startswith("multipart/form-data"):
            form = await request.form()
            try:
                payload = json.loads(form.get("payload") or "{}")
            except json.JSONDecodeError:
                raise HTTPException(400, "payload 不是合法 JSON")
            upload = form.get("photo")
            if upload is None or isinstance(upload, str):
                raise HTTPException(400, "照片题必须附带照片文件")
            item_id = _insert_item(conn, payload, "photo", term, now)
            _save_photo(conn, await upload.read(), item_id, now)
        else:
            try:
                body = await request.json()
            except json.JSONDecodeError:
                raise HTTPException(400, "请求体需为 JSON（错字词）或 multipart（照片题）")
            for f in ("prompt", "wrong_text", "correct_text"):
                if not str(body.get(f) or "").strip():
                    raise HTTPException(400, f"错字词缺少必填字段：{f}")
            if body.get("subject") not in ("chinese", "english", "other"):
                raise HTTPException(400, "错字词科目仅支持 语文 / 英语 / 其他")
            item_id = _insert_item(conn, body, "word", term, now)
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        conn.commit()
        return _row(conn, row)
    finally:
        conn.close()


@api.get("/items")
def list_items(subject: Optional[str] = None, kp_id: Optional[int] = None,
               status: Optional[str] = None, term: Optional[str] = None,
               stubborn: Optional[int] = 0, q: Optional[str] = None,
               due: Optional[str] = None, page: int = 1, page_size: int = 30,
               order: str = "due"):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    conn = db.get_conn()
    try:
        where, args = [], []

        def W(cond, *a):
            where.append(cond)
            args.extend(a)

        if subject in SUBJECTS:
            W("subject=?", subject)
        if kp_id:
            W("kp_id=?", kp_id)
        if status in STATUSES:
            W("status=?", status)
        if term != "all":
            W("term=?", term or db.settings_get(conn, "current_term"))
        if stubborn:
            W("status='reviewing' AND wrong_count>=3")
        if due == "today":
            W("status='reviewing' AND due_date<=?", engine.today().isoformat())
        if q:
            like = f"%{q}%"
            W("(prompt LIKE ? OR wrong_text LIKE ? OR correct_text LIKE ?"
              " OR wrong_answer LIKE ? OR solution LIKE ?)", like, like, like, like, like)
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) c FROM items{cond}", args).fetchone()["c"]
        order_sql = {
            "due": "ORDER BY (status='reviewing') DESC, (wrong_count>=3) DESC, due_date ASC, created_at DESC",
            "new": "ORDER BY created_at DESC",
            "old": "ORDER BY created_at ASC",
        }.get(order, "ORDER BY created_at DESC")
        rows = conn.execute(f"SELECT * FROM items{cond} {order_sql} LIMIT ? OFFSET ?",
                            (*args, page_size, (page - 1) * page_size)).fetchall()
        return {"total": total, "page": page, "page_size": page_size,
                "items": [_row(conn, r) for r in rows]}
    finally:
        conn.close()


@api.patch("/items/{item_id}")
def patch_item(item_id: int, body: dict = Body(...)):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "错题不存在")
        body = dict(body)
        action = body.pop("action", None)

        sets, vals = [], []
        if "subject" in body and body["subject"] in SUBJECTS:
            sets.append("subject=?"); vals.append(body["subject"])
        if "kp_id" in body:
            kp_id = body["kp_id"] or None
            if kp_id and not conn.execute("SELECT 1 FROM knowledge_points WHERE id=?",
                                          (kp_id,)).fetchone():
                raise HTTPException(400, "知识点不存在")
            sets.append("kp_id=?"); vals.append(kp_id)
        if "reason" in body:
            r = body["reason"] or None
            if r is not None and r not in REASONS:
                raise HTTPException(400, "错因不合法")
            sets.append("reason=?"); vals.append(r)
        for f, n in (("wrong_answer", 500), ("solution", 1000), ("prompt", 200),
                     ("wrong_text", 200), ("correct_text", 200)):
            if f in body:
                sets.append(f"{f}=?"); vals.append(_clip(body[f], n))

        if action == "pause":
            if row["status"] != "reviewing":
                raise HTTPException(400, "仅复习中的题可暂停")
            sets.append("status=?"); vals.append("paused")
        elif action == "resume":
            if row["status"] != "paused":
                raise HTTPException(400, "仅已暂停的题可恢复")
            sets.append("status=?"); vals.append("reviewing")
        elif action == "retest":
            if row["status"] != "mastered":
                raise HTTPException(400, "仅已消灭的题可再考一次")
            sets += ["status=?", "due_date=?"]
            vals += ["reviewing", engine.add_days(engine.today(), 1).isoformat()]
        elif action is not None:
            raise HTTPException(400, "不支持的操作")

        if sets:
            conn.execute(f"UPDATE items SET {', '.join(sets)} WHERE id=?", (*vals, item_id))
        conn.commit()
        return _row(conn, conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())
    finally:
        conn.close()


@api.delete("/items/{item_id}")
def delete_item(item_id: int):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "错题不存在")
        photos = conn.execute("SELECT * FROM photos WHERE item_id=?", (item_id,)).fetchall()
        conn.execute("DELETE FROM star_ledger WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM items WHERE id=?", (item_id,))  # photos/logs 级联删除
        for p in photos:
            Path(p["orig_path"]).unlink(missing_ok=True)
            Path(p["thumb_path"]).unlink(missing_ok=True)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- 复习判定 ----------

@api.post("/items/{item_id}/review")
def review_item(item_id: int, body: dict = Body(...)):
    result = body.get("result")
    judged_by = body.get("judged_by", "child")
    if result not in ("pass", "hint", "fail"):
        raise HTTPException(400, "判定结果不合法")
    if judged_by not in ("child", "parent"):
        raise HTTPException(400, "判定人不合法")
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "错题不存在")
        if row["status"] != "reviewing":
            raise HTTPException(400, "该题不在复习中（已消灭或已暂停）")
        if row["due_date"] and row["due_date"] > engine.today().isoformat():
            raise HTTPException(400, "该题尚未到期，不能提前考")
        now = db.now_str()
        upd = engine.compute_judgment(row, result, engine.today())
        # 消灭星星只发一次：再考后重新消灭不重复发
        star_events = [] if (upd["mastered"] and row["mastered_at"]) else list(upd["stars"])
        verify_on = db.settings_get(conn, "verify_enabled", "true") == "true"
        pending = 1 if (result == "pass" and judged_by == "child" and verify_on) else 0

        conn.execute(
            "UPDATE items SET stage=?, due_date=?, status=?, wrong_count=?,"
            " mastered_at=COALESCE(mastered_at,?) WHERE id=?",
            (upd["stage"], upd["due_date"], upd["status"], upd["wrong_count"],
             now if upd["mastered"] else None, item_id))
        conn.execute("INSERT INTO review_log(item_id, result, judged_by, pending_verify,"
                     " created_at) VALUES(?,?,?,?,?)", (item_id, result, judged_by, pending, now))
        for ev, n in star_events:
            engine.add_stars(conn, ev, item_id, n, now)
        bonus = False
        if engine.should_award_day_bonus(conn, engine.today().isoformat()):
            engine.add_stars(conn, "day_all", None, 2, now)
            bonus = True
        conn.commit()
        new = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return {"item": _row(conn, new), "stars": star_events, "day_bonus": bonus,
                "remaining_due": engine.remaining_due(conn, engine.today().isoformat())}
    finally:
        conn.close()


@api.post("/review/{log_id}/verify")
def verify_review(log_id: int, body: dict = Body(...)):
    """家长抽查：approve=通过；驳回则该次判定按「又错」处理并追回 1 颗星"""
    approve = bool(body.get("approve"))
    conn = db.get_conn()
    try:
        log = conn.execute("SELECT * FROM review_log WHERE id=?", (log_id,)).fetchone()
        if not log or not log["pending_verify"]:
            raise HTTPException(404, "待复核记录不存在")
        item = conn.execute("SELECT * FROM items WHERE id=?", (log["item_id"],)).fetchone()
        now = db.now_str()
        conn.execute("UPDATE review_log SET pending_verify=0 WHERE id=?", (log_id,))
        if not approve:
            upd = engine.compute_judgment(item, "fail", engine.today())
            conn.execute("UPDATE items SET stage=?, due_date=?, status=?, wrong_count=?"
                         " WHERE id=?", (upd["stage"], upd["due_date"], upd["status"],
                                         upd["wrong_count"], item["id"]))
            conn.execute("INSERT INTO review_log(item_id, result, judged_by, pending_verify,"
                         " created_at) VALUES(?,?,?,0,?)",
                         (item["id"], "fail", "parent", now))
            engine.add_stars(conn, "verify_reject", item["id"], -1, now)
        conn.commit()
        return {"item": _row(conn, conn.execute("SELECT * FROM items WHERE id=?",
                                                (item["id"],)).fetchone())}
    finally:
        conn.close()


# ---------- 统计 / 我的 ----------

@api.get("/stats")
def stats(term: Optional[str] = None):
    conn = db.get_conn()
    try:
        t = term or db.settings_get(conn, "current_term")
        one = lambda sql, a=(): conn.execute(sql, a).fetchone()  # noqa: E731
        total = one("SELECT COUNT(*) c FROM items WHERE term=?", (t,))["c"]
        mastered = one("SELECT COUNT(*) c FROM items WHERE term=? AND status='mastered'", (t,))["c"]
        reviewing = one("SELECT COUNT(*) c FROM items WHERE term=? AND status='reviewing'", (t,))["c"]
        paused = one("SELECT COUNT(*) c FROM items WHERE term=? AND status='paused'", (t,))["c"]
        stubborn = one("SELECT COUNT(*) c FROM items WHERE term=? AND status='reviewing'"
                       " AND wrong_count>=3", (t,))["c"]
        rate = round(mastered * 100 / total) if total else 0
        top5 = [dict(r) for r in conn.execute(
            "SELECT COALESCE(k.name,'未分类知识点') name, COUNT(*) c,"
            " SUM(i.status='reviewing' AND i.wrong_count>=3) stubborn"
            " FROM items i LEFT JOIN knowledge_points k ON k.id=i.kp_id"
            " WHERE i.term=? GROUP BY i.kp_id ORDER BY c DESC, name LIMIT 5", (t,))]
        reasons = [dict(r) for r in conn.execute(
            "SELECT COALESCE(reason,'未归因') key, COUNT(*) c FROM items WHERE term=?"
            " GROUP BY reason ORDER BY c DESC", (t,))]
        subjects = [dict(r) for r in conn.execute(
            "SELECT subject, COUNT(*) c, SUM(status='mastered') mastered"
            " FROM items WHERE term=? GROUP BY subject ORDER BY c DESC", (t,))]
        pending = [dict(r) for r in conn.execute(
            "SELECT l.id log_id, l.created_at, i.id item_id, i.subject, i.item_type,"
            " i.prompt, i.wrong_text, i.correct_text, i.wrong_answer, i.solution,"
            " i.stage, COALESCE(k.name,'') kp_name"
            " FROM review_log l JOIN items i ON i.id=l.item_id"
            " LEFT JOIN knowledge_points k ON k.id=i.kp_id"
            " WHERE l.pending_verify=1 ORDER BY l.created_at LIMIT 20")]
        terms = [r["term"] for r in conn.execute(
            "SELECT DISTINCT term FROM items ORDER BY term DESC")]
        if t not in terms:
            terms.insert(0, t)
        return {"term": t, "terms": terms, "total": total, "mastered": mastered,
                "reviewing": reviewing, "paused": paused, "stubborn": stubborn, "rate": rate,
                "top5": top5, "reasons": reasons, "subjects": subjects, "pending": pending,
                "streak": engine.streak(conn, engine.today().isoformat()),
                "backup": backup.backup_status()}
    finally:
        conn.close()


@api.get("/me")
def me():
    conn = db.get_conn()
    try:
        t_iso = engine.today().isoformat()
        stars = engine.total_stars(conn)
        stars_today = conn.execute(
            "SELECT COALESCE(SUM(stars),0) s FROM star_ledger WHERE substr(created_at,1,10)=?",
            (t_iso,)).fetchone()["s"]
        glory = [_row(conn, r) for r in conn.execute(
            "SELECT * FROM items WHERE status='mastered' ORDER BY mastered_at DESC LIMIT 50")]
        return {"stars": stars, "stars_today": stars_today,
                "streak": engine.streak(conn, t_iso),
                "badges": engine.compute_badges(conn), "glory": glory,
                "reward_rule": db.settings_get(conn, "reward_rule", "")}
    finally:
        conn.close()


# ---------- 设置 ----------

@api.put("/settings")
def put_settings(body: dict = Body(...)):
    conn = db.get_conn()
    try:
        if "daily_limit" in body:
            try:
                v = int(body["daily_limit"])
            except (TypeError, ValueError):
                raise HTTPException(400, "每日上限必须是数字")
            db.settings_set(conn, "daily_limit", max(3, min(15, v)))
        if "verify_enabled" in body:
            db.settings_set(conn, "verify_enabled", "true" if body["verify_enabled"] else "false")
        if "reward_rule" in body:
            db.settings_set(conn, "reward_rule", _clip(body["reward_rule"], 300) or "")
        if "current_term" in body:
            t = _clip(body["current_term"], 20)
            if not t:
                raise HTTPException(400, "学期标签不能为空")
            db.settings_set(conn, "current_term", t)
        if "new_access_code" in body:
            new = str(body["new_access_code"]).strip()
            old = str(body.get("old_access_code", "")).strip()
            if not (6 <= len(new) <= 12 and new.isalnum()):
                raise HTTPException(400, "访问码需 6~12 位数字或字母")
            expected = db.settings_get(conn, "access_code_hash") or ""
            if not old or not hmac.compare_digest(auth.hash_code(old), expected):
                raise HTTPException(400, "旧访问码不正确")
            db.settings_set(conn, "access_code_hash", auth.hash_code(new))
        conn.commit()
        return {"ok": True,
                "daily_limit": int(db.settings_get(conn, "daily_limit", "8") or 8),
                "verify_enabled": db.settings_get(conn, "verify_enabled") == "true",
                "current_term": db.settings_get(conn, "current_term"),
                "reward_rule": db.settings_get(conn, "reward_rule", "")}
    finally:
        conn.close()


# ---------- 图片服务 ----------

@photos_api.get("/photos/{photo_id}")
def get_photo(photo_id: int, size: Optional[str] = None):
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "照片不存在")
    p = Path(row["thumb_path"] if size == "thumb" else row["orig_path"])
    if not p.exists():
        raise HTTPException(404, "照片文件缺失")
    return FileResponse(p, media_type="image/jpeg")


# ---------- PDF 导出 ----------

@api.post("/export/pdf")
def export_pdf(body: dict = Body(...)):
    template = body.get("template")
    if template not in ("collection", "practice"):
        raise HTTPException(400, "模板必须为 collection 或 practice")
    conn = db.get_conn()
    try:
        now = db.now_str()
        term = body.get("term") or db.settings_get(conn, "current_term")
        item_ids = body.get("item_ids") or []
        where, args = [], []
        if item_ids:
            ph = ",".join("?" * len(item_ids))
            where.append(f"id IN ({ph})")
            args.extend(item_ids)
        else:
            where.append("term=?"); args.append(term)
            if body.get("subject") in SUBJECTS:
                where.append("subject=?"); args.append(body["subject"])
            if body.get("kp_id"):
                where.append("kp_id=?"); args.append(body["kp_id"])
            if body.get("status") in STATUSES:
                where.append("status=?"); args.append(body["status"])
            if body.get("stubborn"):
                where.append("status='reviewing' AND wrong_count>=3")
            if body.get("due") == "today":
                where.append("status='reviewing' AND due_date<=?")
                args.append(engine.today().isoformat())
        rows = conn.execute(f"SELECT * FROM items WHERE {' AND '.join(where)}"
                            " ORDER BY (wrong_count>=3) DESC, due_date ASC, created_at ASC",
                            args).fetchall()
        items = [_row(conn, r) for r in rows]
        if not items:
            raise HTTPException(400, "所选范围内没有错题，无法导出")
        ids = [it["id"] for it in items]
        ph = ",".join("?" * len(ids))
        photos = {r["item_id"]: r["orig_path"] for r in conn.execute(
            f"SELECT item_id, orig_path FROM photos WHERE item_id IN ({ph}) ORDER BY id", ids)}
        logs = {}
        for r in conn.execute(f"SELECT item_id, result FROM review_log"
                              f" WHERE item_id IN ({ph}) ORDER BY created_at", ids):
            logs.setdefault(r["item_id"], []).append(r["result"])

        # 封面统计
        total = len(items)
        mastered = sum(1 for i in items if i["status"] == "mastered")
        stubborn = sum(1 for i in items if i.get("stubborn"))
        subject_label = SUBJECTS.get(body.get("subject"), "全部")
        parts = [f"科目：{subject_label}", f"学期：{term}"]
        if body.get("status") in STATUSES:
            parts.append(f"状态：{STATUSES[body['status']]}")
        if body.get("stubborn"):
            parts.append("仅顽固题")
        if item_ids:
            parts.append(f"手动勾选 {len(item_ids)} 题")
        meta = {"subject_label": subject_label, "term": term,
                "stats": {"total": total, "mastered": mastered, "stubborn": stubborn,
                          "rate": round(mastered * 100 / total)},
                "filters_desc": " · ".join(parts),
                "group_by": body.get("group_by", "kp"),
                "shuffle": bool(body.get("shuffle")),
                "stubborn_top": bool(body.get("stubborn_top", True))}
        path = pdfgen.generate(template, items, photos, logs, meta)
        return {"filename": path.name, "size": path.stat().st_size,
                "download_url": f"/api/export/download/{path.name}", "generated_at": now}
    finally:
        conn.close()


@api.get("/export/download/{filename}")
def download_export(filename: str):
    p = (EXPORT_DIR / filename).resolve()
    if p.parent != EXPORT_DIR.resolve() or not p.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(p, media_type="application/pdf", filename=filename)


# ---------- 备份 ----------

@api.post("/backup")
def run_backup():
    return backup.do_backup("manual")


@api.get("/backup/status")
def backup_status():
    return backup.backup_status()


@api.get("/backup/latest/download")
def download_latest_backup():
    latest = backup.latest_backup()
    if not latest:
        raise HTTPException(404, "还没有备份文件")
    return FileResponse(BACKUP_DIR / latest["name"], media_type="application/zip",
                        filename=latest["name"])


# ---------- 路由装配 ----------

app.include_router(public)
app.include_router(api)
app.include_router(photos_api)
app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")

if __name__ == "__main__":
    ensure_dirs()
    print(f"DoOver 错题本 v{APP_VERSION} 启动 → http://localhost:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
