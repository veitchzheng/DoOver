"""API 全链路功能测试（按文件内顺序依次执行，共享一个测试库）"""
import io
import json
import zipfile
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import backup as backup_mod
import db
import engine
import server
from conftest import TEST_CODE, CHILD_CODE


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        c.post("/api/login", json={"code": TEST_CODE})
        yield c


def _force_due(item_id: int):
    """直接改库模拟时间流逝：把题目到期日拉到今天"""
    conn = db.get_conn()
    conn.execute("UPDATE items SET due_date=? WHERE id=?", (engine.today().isoformat(), item_id))
    conn.commit()
    conn.close()


def _png_bytes(text="12 × 8 - 49 = ?"):
    im = Image.new("RGB", (640, 240), "#FFFFFF")
    from PIL import ImageDraw, ImageFont
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype("fonts/NotoSansSC-Regular.ttf", 28)
    d.text((30, 90), text, font=f, fill="#111")
    bio = io.BytesIO()
    im.save(bio, "PNG")
    return bio.getvalue()


def _kp_id(client, subject, name):
    meta = client.get("/api/meta").json()
    for kp in meta["kps"]:
        if kp["subject"] == subject and kp["name"] == name:
            return kp["id"]
    raise AssertionError(f"知识点未预置: {subject}/{name}")


# ---------- 健康与鉴权 ----------

def test_health_no_auth(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok"
    assert "backup" in body and "disk_free_mb" in body


def test_api_requires_login():
    with TestClient(server.app) as c:  # 未登录的新客户端
        assert c.get("/api/today").status_code == 401
        assert c.get("/api/items").status_code == 401
        assert c.get("/photos/1").status_code == 401


def test_login_wrong_code(client):
    assert client.post("/api/login", json={"code": "000000"}).status_code == 401


def test_logout_then_login(client):
    client.post("/api/logout")
    assert client.get("/api/today").status_code == 401
    assert client.post("/api/login", json={"code": TEST_CODE}).status_code == 200
    assert client.get("/api/today").status_code == 200


# ---------- 录入 ----------

def test_meta_has_kps(client):
    meta = client.get("/api/meta").json()
    assert len(meta["kps"]) >= 20
    assert set(meta["subjects"]) == {"math", "chinese", "english", "other"}
    assert meta["settings"]["daily_limit"] == 8


def test_create_word_item(client):
    r = client.post("/api/items", json={
        "subject": "chinese", "kp_id": _kp_id(client, "chinese", "形近字"),
        "reason": "concept", "prompt": "zhì（治理）", "wrong_text": "志", "correct_text": "治"})
    assert r.status_code == 200
    it = r.json()
    assert it["item_type"] == "word" and it["stage"] == 0
    assert it["due_date"] == engine.add_days(engine.today(), 1).isoformat()  # 明日到期
    assert it["kp_name"] == "形近字" and it["stubborn"] is False


def test_create_word_missing_field(client):
    r = client.post("/api/items", json={"subject": "chinese", "prompt": "zhì"})
    assert r.status_code == 400


def test_create_photo_item(client):
    payload = {"subject": "math", "kp_id": _kp_id(client, "math", "应用题"),
               "reason": "careless", "wrong_answer": "最后一步算错", "solution": "先乘除后加减"}
    r = client.post("/api/items", data={"payload": json.dumps(payload)},
                    files={"photo": ("test.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    it = r.json()
    assert it["item_type"] == "photo" and len(it["photo_ids"]) == 1
    pid = it["photo_ids"][0]
    assert client.get(f"/photos/{pid}").status_code == 200
    assert client.get(f"/photos/{pid}?size=thumb").status_code == 200
    assert client.get(f"/photos/{pid}").headers["content-type"].startswith("image/")
    assert client.get("/photos/99999").status_code == 404


def test_create_photo_without_file(client):
    r = client.post("/api/items", data={"payload": "{}"})
    assert r.status_code == 400


def test_items_list_and_filters(client):
    r = client.get("/api/items", params={"subject": "chinese"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1 and all(i["subject"] == "chinese" for i in body["items"])
    r2 = client.get("/api/items", params={"q": "治理"})
    assert r2.json()["total"] == 1
    r3 = client.get("/api/items", params={"term": "all"})
    assert r3.json()["total"] >= 2


# ---------- 今日与判定 ----------

def test_today_and_review_pass(client):
    meta = client.get("/api/meta").json()
    assert client.get("/api/today").json()["items"] == []  # 新题明日才到期
    r = client.post("/api/items", json={
        "subject": "english", "kp_id": _kp_id(client, "english", "单词拼写"),
        "reason": "careless", "prompt": "星期三", "wrong_text": "Wensday",
        "correct_text": "Wednesday"})
    item_id = r.json()["id"]
    _force_due(item_id)
    today = client.get("/api/today").json()
    assert today["total_due"] == 1 and today["items"][0]["id"] == item_id

    r = client.post(f"/api/items/{item_id}/review",
                    json={"result": "pass", "judged_by": "parent"})
    body = r.json()
    assert body["item"]["stage"] == 1
    assert body["stars"] == [["pass", 1]]  # 引擎返回 (event, num) 元组，序列化为二维数组
    assert body["day_bonus"] is True  # 今天唯一到期题清空且全 pass
    assert body["remaining_due"] == 0


def test_review_early_guard(client):
    r = client.post("/api/items", json={
        "subject": "english", "prompt": "星期四", "wrong_text": "Thuseday",
        "correct_text": "Thursday", "reason": "clueless"})
    item_id = r.json()["id"]
    assert client.post(f"/api/items/{item_id}/review",
                       json={"result": "pass"}).status_code == 400  # 未到期不能提前考


def test_review_hint(client):
    r = client.post("/api/items", json={
        "subject": "english", "prompt": "星期五", "wrong_text": "Firday",
        "correct_text": "Friday", "reason": "careless"})
    item_id = r.json()["id"]
    _force_due(item_id)
    r = client.post(f"/api/items/{item_id}/review",
                    json={"result": "hint", "judged_by": "child"})
    body = r.json()
    assert body["item"]["stage"] == 0 and body["stars"] == []
    assert body["item"]["due_date"] == engine.add_days(engine.today(), 1).isoformat()


def test_child_pass_pending_verify_then_approve(client):
    r = client.post("/api/items", json={
        "subject": "chinese", "kp_id": _kp_id(client, "chinese", "多音字"),
        "reason": "careless", "prompt": "chéng jì", "wrong_text": "成积", "correct_text": "成绩"})
    item_id = r.json()["id"]
    _force_due(item_id)
    r = client.post(f"/api/items/{item_id}/review",
                    json={"result": "pass", "judged_by": "child"})
    assert r.status_code == 200
    stats = client.get("/api/stats").json()
    assert any(p["item_id"] == item_id for p in stats["pending"])
    log_id = next(p["log_id"] for p in stats["pending"] if p["item_id"] == item_id)
    r = client.post(f"/api/review/{log_id}/verify", json={"approve": True})
    assert r.status_code == 200
    assert client.get("/api/stats").json()["pending"] == []


def test_verify_reject(client):
    r = client.post("/api/items", json={
        "subject": "english", "prompt": "星期六", "wrong_text": "Saterday",
        "correct_text": "Saturday", "reason": "careless"})
    item_id = r.json()["id"]
    _force_due(item_id)
    stars_before = client.get("/api/me").json()["stars"]
    client.post(f"/api/items/{item_id}/review",
                json={"result": "pass", "judged_by": "child"})
    log_id = next(p["log_id"] for p in client.get("/api/stats").json()["pending"]
                  if p["item_id"] == item_id)
    r = client.post(f"/api/review/{log_id}/verify", json={"approve": False})
    body = r.json()
    assert body["item"]["stage"] == 0 and body["item"]["wrong_count"] == 1
    assert client.get("/api/me").json()["stars"] == stars_before + 1 - 1  # +1 后驳回追回


def test_fail_marks_stubborn(client):
    r = client.post("/api/items", json={
        "subject": "english", "kp_id": _kp_id(client, "english", "单词拼写"),
        "reason": "clueless", "prompt": "星期日", "wrong_text": "Sundayy",
        "correct_text": "Sunday"})
    item_id = r.json()["id"]
    for _ in range(3):  # 连错 3 次 → 顽固题
        _force_due(item_id)
        client.post(f"/api/items/{item_id}/review", json={"result": "fail"})
    row = client.get("/api/items", params={"stubborn": 1}).json()["items"]
    assert any(i["id"] == item_id and i["stubborn"] for i in row)
    # 顽固题应在今日清单置顶
    _force_due(item_id)
    today = client.get("/api/today").json()
    assert today["items"][0]["id"] == item_id


def test_master_flow_and_badges(client):
    r = client.post("/api/items", json={
        "subject": "chinese", "kp_id": _kp_id(client, "chinese", "形近字"),
        "reason": "calc", "prompt": "jì suàn（计算）", "wrong_text": "计祘",
        "correct_text": "计算"})
    item_id = r.json()["id"]
    for _ in range(5):  # 五连过 → 消灭
        _force_due(item_id)
        r = client.post(f"/api/items/{item_id}/review",
                        json={"result": "pass", "judged_by": "parent"})
        assert r.status_code == 200
    assert r.json()["item"]["status"] == "mastered"
    assert r.json()["stars"] == [["master", 5]]

    me = client.get("/api/me").json()
    assert any(b["key"] == "first_master" and b["unlocked"] for b in me["badges"])
    assert any(g["id"] == item_id for g in me["glory"])
    # 再考一次 → 回到复习中，明日到期
    r = client.patch(f"/api/items/{item_id}", json={"action": "retest"})
    assert r.json()["status"] == "reviewing"
    # 再消灭不重复发星
    _force_due(item_id)
    r = client.post(f"/api/items/{item_id}/review",
                    json={"result": "pass", "judged_by": "parent"})
    assert r.json()["stars"] == []


def test_daily_limit_cap(client):
    ids = []
    for i in range(4):
        r = client.post("/api/items", json={
            "subject": "chinese", "prompt": f"测试限流{i}", "wrong_text": "错",
            "correct_text": "对", "reason": "careless"})
        ids.append(r.json()["id"])
    for i in ids:
        _force_due(i)
    body = client.get("/api/today").json()
    assert len(body["items"]) == min(body["limit"], body["total_due"])
    assert body["limit"] == 8


# ---------- 编辑与删除 ----------

def test_patch_edit_and_actions(client):
    r = client.post("/api/items", json={
        "subject": "chinese", "prompt": "待编辑", "wrong_text": "甲", "correct_text": "乙",
        "reason": "careless"})
    item_id = r.json()["id"]
    r = client.patch(f"/api/items/{item_id}", json={
        "kp_id": _kp_id(client, "chinese", "形近字"), "reason": "concept",
        "solution": "形声字：氵与水有关"})
    it = r.json()
    assert it["kp_name"] == "形近字" and it["reason_label"] == "概念不会"
    assert it["solution"] == "形声字：氵与水有关"
    # 暂停 → 恢复
    assert client.patch(f"/api/items/{item_id}", json={"action": "pause"}).json()["status"] == "paused"
    assert client.patch(f"/api/items/{item_id}", json={"action": "resume"}).json()["status"] == "reviewing"
    assert client.patch(f"/api/items/{item_id}", json={"action": "pause"}).json()["status"] == "paused"


def test_delete_item(client):
    r = client.post("/api/items", json={
        "subject": "other", "prompt": "待删除UNIQUE123", "wrong_text": "x", "correct_text": "y",
        "reason": "careless"})
    item_id = r.json()["id"]
    assert client.delete(f"/api/items/{item_id}").status_code == 200
    assert client.get("/api/items", params={"q": "待删除UNIQUE123", "term": "all"}).json()["total"] == 0


# ---------- 统计 / 我的 / 设置 ----------

def test_stats_shape(client):
    s = client.get("/api/stats").json()
    for k in ("total", "mastered", "reviewing", "paused", "stubborn", "rate",
              "top5", "reasons", "subjects", "terms", "streak", "backup"):
        assert k in s
    assert s["total"] >= 10 and s["mastered"] >= 1


def test_settings_update(client):
    r = client.put("/api/settings", json={"daily_limit": 5, "reward_rule": "10 颗星=冰淇淋一支"})
    assert r.json()["daily_limit"] == 5
    meta = client.get("/api/meta").json()
    assert meta["settings"]["daily_limit"] == 5
    assert client.put("/api/settings", json={"daily_limit": 100}).json()["daily_limit"] == 15  # 上限钳制
    client.put("/api/settings", json={"daily_limit": 8})
    # 改访问码：旧码不对 → 400；对了 → 生效
    assert client.put("/api/settings", json={
        "new_access_code": "888888", "old_access_code": "wrong"}).status_code == 400
    assert client.put("/api/settings", json={
        "new_access_code": "888888", "old_access_code": TEST_CODE}).status_code == 200
    with TestClient(server.app) as c:
        assert c.post("/api/login", json={"code": TEST_CODE}).status_code == 401
        assert c.post("/api/login", json={"code": "888888"}).status_code == 200
    # 改回来，供后续测试使用
    client.put("/api/settings", json={"new_access_code": TEST_CODE, "old_access_code": "888888"})


# ---------- PDF 导出 ----------

def test_export_collection_pdf(client):
    r = client.post("/api/export/pdf", json={"template": "collection", "group_by": "kp"})
    assert r.status_code == 200
    body = r.json()
    assert body["size"] > 5000 and body["filename"].endswith(".pdf")
    dl = client.get(body["download_url"])
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.content[:5] == b"%PDF-"


def test_export_practice_pdf_with_word_grids(client):
    r = client.post("/api/export/pdf", json={
        "template": "practice", "subject": "chinese", "group_by": "time",
        "shuffle": True, "stubborn_top": False})
    assert r.status_code == 200
    body = r.json()
    assert "重练卷" in body["filename"]
    assert client.get(body["download_url"]).status_code == 200


def test_export_practice_photo_answer_not_leaked(client):
    """回归：照片题在重练卷答案页取自身正解（曾串成前/后一题答案，排第一会直接崩）"""
    r = client.post("/api/items", data={"payload": json.dumps({
        "subject": "math", "kp_id": _kp_id(client, "math", "应用题"),
        "reason": "careless", "wrong_answer": "41 支", "solution": "3×12-15=21 支"})},
        files={"photo": ("p.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    pid = r.json()["id"]
    wid = client.post("/api/items", json={"subject": "english", "prompt": "because（因为）",
                                          "wrong_text": "becuase", "correct_text": "because",
                                          "kp_name": "单词拼写"}).json()["id"]
    r = client.post("/api/export/pdf", json={
        "template": "practice", "group_by": "time", "stubborn_top": False,
        "item_ids": [pid, wid]})
    assert r.status_code == 200
    import pymupdf
    pdf = client.get(r.json()["download_url"]).content
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    flat = "".join(p.get_text() for p in doc).replace("\n", " ")
    doc.close()
    assert "【数学 · 应用题】 3×12-15=21 支" in flat    # 第 1 题（照片题）答案=自身正解
    assert "【应用题】 because" not in flat              # 不串成相邻错词题的答案
    assert "【英语 · 单词拼写】 because" in flat         # 错词题答案正确


def test_export_empty_scope(client):
    assert client.post("/api/export/pdf", json={
        "template": "practice", "subject": "other", "term": "不存在的学期"}).status_code == 400


# ---------- 备份 ----------

def test_manual_backup_and_download(client):
    (backup_mod.DATA_DIR / "llm.json").write_text('{"active": "test"}', encoding="utf-8")
    r = client.post("/api/backup")
    assert r.status_code == 200
    body = r.json()
    assert body["name"].startswith("doover-backup-") and body["size"] > 1000
    status = client.get("/api/backup/status").json()
    assert status["count"] >= 1 and status["latest"]["name"] == body["name"]
    dl = client.get("/api/backup/latest/download")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"


def test_backup_verify_and_restore(client):
    """解压校验 + 恢复演练：备份应包含完整可用的库与照片文件"""
    import tempfile
    from pathlib import Path
    latest = backup_mod.latest_backup()
    assert latest, "应已有备份"
    tmp = tempfile.mkdtemp(prefix="doover-restore-")
    result = backup_mod.verify_backup(Path(backup_mod.BACKUP_DIR) / latest["name"], dest=tmp)
    assert result["ok"] is True, result
    assert result["counts"]["items"] >= 10
    with zipfile.ZipFile(Path(backup_mod.BACKUP_DIR) / latest["name"]) as zf:
        names = zf.namelist()
        assert "doover.db" in names and "manifest.json" in names
        assert "llm.json" in names                          # AI 配置随备份走，恢复即用
        assert any(n.startswith("photos/") for n in names)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ---------- 家长/孩子分端隔离 ----------

def test_parent_role_meta(client):
    meta = client.get("/api/meta").json()
    assert meta["role"] == "parent"


def test_child_login_and_isolation(client):
    """孩子码登录 → 仅复习相关接口；家长接口一律 403"""
    with TestClient(server.app) as c:
        r = c.post("/api/login", json={"code": CHILD_CODE})
        assert r.status_code == 200 and r.json()["role"] == "child"
        meta = c.get("/api/meta").json()
        assert meta["role"] == "child"
        # 孩子可用：今日、我的、meta、判定、提示、单题
        assert c.get("/api/today").status_code == 200
        assert c.get("/api/me").status_code == 200
        assert c.get("/api/llm/status").status_code == 200
        # 家长专属：越权全部 403（后端硬隔离，不依赖 UI 隐藏）
        assert c.post("/api/items", json={
            "subject": "chinese", "prompt": "p", "wrong_text": "x",
            "correct_text": "y"}).status_code == 403
        assert c.get("/api/items").status_code == 403
        assert c.get("/api/stats").status_code == 403
        assert c.put("/api/settings", json={"daily_limit": 5}).status_code == 403
        assert c.post("/api/backup").status_code == 403
        assert c.post("/api/redeem/1/approve").status_code == 403   # 审批兑换是家长专属
        assert c.post("/api/export/pdf", json={"template": "collection"}).status_code == 403
        assert c.post("/api/review/1/verify", json={"approve": True}).status_code == 403
        assert c.post("/api/items/1/analyze").status_code == 403
        assert c.delete("/api/items/1").status_code == 403


def test_child_can_review_but_not_manage(client):
    """孩子能正常做题判定（核心场景），但不能改题/删题"""
    r = client.post("/api/items", json={   # 家长录入
        "subject": "chinese", "prompt": "zhì", "wrong_text": "志", "correct_text": "治"})
    iid = r.json()["id"]
    _force_due(iid)
    with TestClient(server.app) as c:
        c.post("/api/login", json={"code": CHILD_CODE})
        body = {"result": "pass", "judged_by": "child"}
        r2 = c.post(f"/api/items/{iid}/review", json=body)
        assert r2.status_code == 200                       # 判定是孩子的主操作
        assert r2.json()["item"]["stage"] == 1
        assert c.patch(f"/api/items/{iid}", json={"solution": "改"}).status_code == 403


def test_child_code_management(client):
    """家长可改孩子码；改后旧码失效、新码可用"""
    r = client.put("/api/settings", json={"new_child_code": "999888"})
    assert r.status_code == 200
    with TestClient(server.app) as c:
        assert c.post("/api/login", json={"code": CHILD_CODE}).status_code == 401   # 旧码失效
        assert c.post("/api/login", json={"code": "999888"}).json()["role"] == "child"
    # 还原，避免影响其他用例
    client.put("/api/settings", json={"new_child_code": CHILD_CODE})


def test_parent_code_unchanged_for_existing_install(client):
    """升级兼容：原访问码继续作为家长码使用"""
    r = client.post("/api/login", json={"code": TEST_CODE})
    assert r.status_code == 200 and r.json()["role"] == "parent"


# ---------- 星星兑换（孩子申请 · 家长审批闭环） ----------

def test_redeem_flow_and_balance(client):
    """申请只登记不扣星；家长批准后才扣，可用余额减少、累计赚得不变"""
    conn = db.get_conn()
    engine.add_stars(conn, "master", None, 30, db.now_str())   # 直接补 30 星做基数
    conn.commit()
    conn.close()
    before = client.get("/api/me").json()
    base_bal, base_earned = before["stars"], before["stars_earned"]
    assert base_bal >= 20 and base_earned >= 20
    assert client.post("/api/redeem", json={"note": "周末选一部电影", "stars": 20}).status_code == 200
    me = client.get("/api/me").json()
    rd = next(x for x in me["redeems"] if x["note"] == "周末选一部电影")
    assert rd["status"] == "pending"
    assert me["stars"] == base_bal and me["stars_earned"] == base_earned   # 申请不扣星
    assert client.post(f"/api/redeem/{rd['id']}/approve").status_code == 200
    me = client.get("/api/me").json()
    assert me["stars"] == base_bal - 20
    assert me["stars_earned"] == base_earned                                # 累计赚得只增不减
    conn = db.get_conn()
    assert conn.execute("SELECT stars FROM star_ledger WHERE event='redeem'"
                        ).fetchone()["stars"] == -20                       # 负数流水入账
    conn.close()
    assert client.post(f"/api/redeem/{rd['id']}/approve").status_code == 400  # 重复处理
    assert client.post("/api/redeem/99999/approve").status_code == 404


def test_redeem_validation_and_limits(client):
    """校验：空描述/非法星数/超额拒绝；驳回不扣星；待审批上限、孩子撤回与越权"""
    bal = client.get("/api/me").json()["stars"]
    assert client.post("/api/redeem", json={"note": "", "stars": 10}).status_code == 400
    assert client.post("/api/redeem", json={"note": "零颗", "stars": 0}).status_code == 400
    assert client.post("/api/redeem", json={"note": "布尔", "stars": True}).status_code == 400
    assert client.post("/api/redeem", json={"note": "超额愿望", "stars": bal + 1}).status_code == 400
    # 驳回：星星从未预扣，余额不变；已处理的申请不可再处理
    client.post("/api/redeem", json={"note": "驳回测试", "stars": 5})
    rid = next(x["id"] for x in client.get("/api/me").json()["redeems"]
               if x["note"] == "驳回测试")
    assert client.post(f"/api/redeem/{rid}/reject").status_code == 200
    assert client.get("/api/me").json()["stars"] == bal
    assert client.post(f"/api/redeem/{rid}/reject").status_code == 400
    # 待审批最多 3 条，防申请刷屏
    for i in range(3):
        assert client.post("/api/redeem", json={"note": f"排队{i}", "stars": 1}).status_code == 200
    assert client.post("/api/redeem", json={"note": "超限", "stars": 1}).status_code == 400
    # 孩子可撤回自己的待审批申请；审批/驳回是家长专属（后端硬隔离）
    with TestClient(server.app) as c:
        c.post("/api/login", json={"code": CHILD_CODE})
        pend = [x for x in c.get("/api/me").json()["redeems"] if x["status"] == "pending"]
        assert c.delete(f"/api/redeem/{pend[0]['id']}").status_code == 200
        assert c.delete(f"/api/redeem/{pend[0]['id']}").status_code == 404      # 已不存在
        assert c.post(f"/api/redeem/{pend[1]['id']}/approve").status_code == 403
        assert c.post(f"/api/redeem/{pend[1]['id']}/reject").status_code == 403
    # 家长清场：剩余待审批全部驳回
    for x in client.get("/api/me").json()["redeems"]:
        if x["status"] == "pending":
            client.post(f"/api/redeem/{x['id']}/reject")
