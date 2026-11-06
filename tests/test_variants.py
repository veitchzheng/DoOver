"""变式题（消灭彩蛋）测试：生成触发、家长预审、孩子挑战 +2 星、越权与一次性"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import db
import engine
import llm
import server
from conftest import TEST_CODE, CHILD_CODE


@pytest.fixture
def client():
    """独立客户端；mock 变式生成，屏蔽启动补分析"""
    orig = server._backfill_analysis
    server._backfill_analysis = lambda: None
    with TestClient(server.app) as c:
        c.post("/api/login", json={"code": TEST_CODE})
        yield c
    server._backfill_analysis = orig
    remove_cfg()


def remove_cfg():
    llm.LLM_FILE.unlink(missing_ok=True)


def write_cfg():
    llm.LLM_FILE.write_text(json.dumps({
        "active": "fake",
        "providers": {"fake": {"label": "Fake", "base_url": "http://localhost:1/v1",
                               "api_key": "sk-fake", "model": "fake-chat"}},
    }), encoding="utf-8")


def _make_due_and_review_to_mastered(client, subject="chinese", kp=None, stage=4):
    """造一道处于第 5 档前夜的错题并判定通过 → 消灭"""
    iid = client.post("/api/items", json={
        "subject": subject, "prompt": "p", "wrong_text": "x", "correct_text": "y",
        **({"kp_name": kp} if kp else {})}).json()["id"]
    conn = db.get_conn()
    conn.execute("UPDATE items SET stage=?, due_date=? WHERE id=?",
                 (stage, engine.today().isoformat(), iid))
    conn.commit()
    conn.close()
    return iid


def _wait_variant(client, iid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = client.get(f"/api/items/{iid}/variant").json()
        if v.get("exists"):
            return v
        time.sleep(0.05)
    raise AssertionError("变式题生成超时")


def test_variant_generated_on_mastered(client, monkeypatch):
    """消灭判定自动触发变式生成；预审默认开 → pending"""
    write_cfg()
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"question": "默写：zhì（制服）", "answer": "制，与制作有关"}')
    iid = _make_due_and_review_to_mastered(client, kp="形近字")
    r = client.post(f"/api/items/{iid}/review", json={"result": "pass", "judged_by": "parent"})
    assert r.json()["item"]["status"] == "mastered"
    v = _wait_variant(client, iid)
    assert v["variant"]["status"] == "pending"
    assert "制服" in v["variant"]["question"]
    assert v["variant"]["model"] == "fake-chat"


def test_variant_requires_llm_and_kp(client):
    """未配置模型静默跳过；无知识点不生成"""
    remove_cfg()
    iid = _make_due_and_review_to_mastered(client)   # 不带 kp
    client.post(f"/api/items/{iid}/review", json={"result": "pass", "judged_by": "parent"})
    assert client.get(f"/api/items/{iid}/variant").json() == {"exists": False}
    write_cfg()
    iid2 = _make_due_and_review_to_mastered(client)   # 有模型但无 kp
    client.post(f"/api/items/{iid2}/review", json={"result": "pass", "judged_by": "parent"})
    time.sleep(0.5)
    assert client.get(f"/api/items/{iid2}/variant").json() == {"exists": False}


def test_variant_gate_flow_approve_judge(client, monkeypatch):
    """家长预审 → 放行 → 孩子挑战 +2 星 → 一次性锁定 → 星星入账"""
    write_cfg()
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"question": "口算：25×4-30=?", "answer": "70，先乘后减"}')
    iid = _make_due_and_review_to_mastered(client, subject="chinese", kp="多音字")
    client.post(f"/api/items/{iid}/review", json={"result": "pass", "judged_by": "parent"})
    v = _wait_variant(client, iid)
    vid = v["variant"]["id"]

    # 孩子此时只能看到锁定态
    with TestClient(server.app) as c:
        c.post("/api/login", json={"code": CHILD_CODE})
        locked = c.get(f"/api/items/{iid}/variant").json()
        assert locked["exists"] is True and locked["locked"] is True
        assert "question" not in locked.get("variant", {})
        # 未放行判定 → 403；孩子不能审阅放行 → 403
        assert c.post(f"/api/variants/{vid}/judge", json={"result": "pass"}).status_code == 403
        assert c.post(f"/api/variants/{vid}/review",
                      json={"action": "approve"}).status_code == 403

    # 家长放行
    assert client.post(f"/api/variants/{vid}/review",
                       json={"action": "approve"}).status_code == 200
    with TestClient(server.app) as c:
        c.post("/api/login", json={"code": CHILD_CODE})
        ok = c.get(f"/api/items/{iid}/variant").json()
        assert ok["locked"] is False
        before = c.get("/api/me").json()["stars"]
        j = c.post(f"/api/variants/{vid}/judge", json={"result": "pass"}).json()
        assert j["stars"] == 2
        assert c.get("/api/me").json()["stars"] == before + 2
        # 重复挑战 → 409
        assert c.post(f"/api/variants/{vid}/judge",
                      json={"result": "pass"}).status_code == 409
        # 挑战后回看：锁定
        assert c.get(f"/api/items/{iid}/variant").json()["locked"] is True


def test_variant_gate_off_auto_approve(client, monkeypatch):
    """预审关闭 → 生成即放行，孩子可直接挑战"""
    write_cfg()
    client.put("/api/settings", json={"variant_gate": False})
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"question": "q", "answer": "a"}')
    iid = _make_due_and_review_to_mastered(client, kp="形近字")
    client.post(f"/api/items/{iid}/review", json={"result": "pass", "judged_by": "parent"})
    v = _wait_variant(client, iid)
    assert v["variant"]["status"] == "approved"
    client.put("/api/settings", json={"variant_gate": True})


def test_variant_manual_generate_and_regenerate(client, monkeypatch):
    """家长手动生成（老数据补生成）/驳回/重新生成"""
    write_cfg()
    calls = []
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None: (
        calls.append(1) or '{"question": "手生成的题", "answer": "手生成的答案"}'))
    # 直接造一道已消灭但无变式的老题（不走判定触发）
    iid = client.post("/api/items", json={
        "subject": "chinese", "prompt": "p", "wrong_text": "x", "correct_text": "y",
        "kp_name": "形近字"}).json()["id"]
    conn = db.get_conn()
    conn.execute("UPDATE items SET status='mastered', mastered_at=? WHERE id=?",
                 (db.now_str(), iid))
    conn.commit()
    conn.close()
    assert client.post(f"/api/items/{iid}/variant/generate").status_code == 200
    v = _wait_variant(client, iid)
    vid = v["variant"]["id"]
    assert "手生成的题" in v["variant"]["question"]
    n_before = len(calls)
    # 重复生成 → 409（已存在）
    assert client.post(f"/api/items/{iid}/variant/generate").status_code == 409
    # 驳回（删除）→ 再手动生成 → 新题
    client.post(f"/api/variants/{vid}/review", json={"action": "reject"})
    assert client.get(f"/api/items/{iid}/variant").json() == {"exists": False}
    assert client.post(f"/api/items/{iid}/variant/generate").status_code == 200
    _wait_variant(client, iid)
    assert len(calls) > n_before


def test_variant_judge_fail_no_stars(client, monkeypatch):
    """挑战失败 0 星但消耗次数（防反复挑战刷星）"""
    write_cfg()
    client.put("/api/settings", json={"variant_gate": False})
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"question": "q2", "answer": "a2"}')
    iid = _make_due_and_review_to_mastered(client, kp="形近字")
    client.post(f"/api/items/{iid}/review", json={"result": "pass", "judged_by": "parent"})
    v = _wait_variant(client, iid)
    before = client.get("/api/me").json()["stars"]
    j = client.post(f"/api/variants/{v['variant']['id']}/judge",
                    json={"result": "fail"}).json()
    assert j["stars"] == 0
    assert client.get("/api/me").json()["stars"] == before
    client.put("/api/settings", json={"variant_gate": True})


# ---------- 重练卷导出附变式题（检验纠错效果，一次性生成不入库） ----------

def _mk_item(client, kp=None, subject="chinese"):
    return client.post("/api/items", json={
        "subject": subject, "prompt": "p", "wrong_text": "x", "correct_text": "y",
        **({"kp_name": kp} if kp else {})}).json()["id"]


def test_export_practice_variants_by_kp(client, monkeypatch):
    """重练卷勾选变式：按知识点去重各出一道（无知识点的不算），题面与答案都进 PDF"""
    write_cfg()
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"question": "默写：zhì（制服）", "answer": "制"}')
    ids = [_mk_item(client, kp="形近字"), _mk_item(client, kp="形近字"),
           _mk_item(client, kp="多音字"), _mk_item(client)]  # 第 4 题无知识点
    r = client.post("/api/export/pdf", json={
        "template": "practice", "variants": True, "group_by": "time",
        "stubborn_top": False, "item_ids": ids})
    assert r.status_code == 200
    assert r.json()["variants"] == 2  # 形近字、多音字各一道
    # PDF 实际印出了变式题区与末页答案
    import pymupdf
    pdf = client.get(r.json()["download_url"]).content
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = "".join(p.get_text() for p in doc)
    doc.close()
    assert "举一反三" in text
    assert "变1." in text and "变2." in text and "变3." not in text
    assert "形近字" in text          # 变1 答案行：【形近字】 制


def test_export_collection_ignores_variants(client, monkeypatch):
    """收藏本即使带了 variants 参数也忽略"""
    write_cfg()
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"question": "q", "answer": "a"}')
    ids = [_mk_item(client, kp="形近字")]
    r = client.post("/api/export/pdf", json={
        "template": "collection", "variants": True, "item_ids": ids})
    assert r.status_code == 200
    assert r.json()["variants"] == 0


def test_export_variants_llm_failure_skips(client, monkeypatch):
    """生成全失败：跳过变式，整卷照常导出"""
    write_cfg()
    def boom(m, cfg=None):
        raise RuntimeError("模型不可用")
    monkeypatch.setattr(llm, "chat", boom)
    ids = [_mk_item(client, kp="形近字"), _mk_item(client, kp="多音字")]
    r = client.post("/api/export/pdf", json={
        "template": "practice", "variants": True, "item_ids": ids})
    assert r.status_code == 200
    assert r.json()["variants"] == 0


def test_export_variants_without_llm(client):
    """未配置模型：请求了变式也静默跳过，不报错"""
    remove_cfg()
    ids = [_mk_item(client, kp="形近字")]
    r = client.post("/api/export/pdf", json={
        "template": "practice", "variants": True, "item_ids": ids})
    assert r.status_code == 200
    assert r.json()["variants"] == 0
