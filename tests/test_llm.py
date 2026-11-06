"""AI 错题分析（llm.py + 录入自动分析链路）测试

运行顺序说明：pytest 按文件名序执行，本文件在 test_api.py 之后运行；
所有 LLM 网络调用都被 mock，不会打真实接口、不产生费用。
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import db
import engine
import llm
import server
from conftest import TEST_CODE


# ---------- 工具 ----------

def write_llm_config(active="fake", api_key="sk-fake", vision=False, vision_key=""):
    cfg = {
        "active": active,
        "providers": {
            "fake": {"label": "FakeLLM", "base_url": "http://localhost:1/v1",
                     "api_key": api_key, "model": "fake-chat"},
            "empty_key": {"label": "NoKey", "base_url": "http://localhost:1/v1",
                          "api_key": "", "model": "x"},
        },
    }
    if vision:
        cfg["vision_active"] = "fake_vision"
        cfg["providers"]["fake_vision"] = {
            "label": "FakeVision", "base_url": "http://localhost:1/v1",
            "api_key": vision_key or "sk-vfake", "model": "fake-vl", "vision": True}
    llm.LLM_FILE.write_text(json.dumps(cfg), encoding="utf-8")


def remove_llm_config():
    llm.LLM_FILE.unlink(missing_ok=True)


@pytest.fixture
def client():
    """独立 TestClient；为确定性屏蔽启动补分析线程"""
    remove_llm_config()
    orig = server._backfill_analysis
    server._backfill_analysis = lambda: None
    with TestClient(server.app) as c:
        c.post("/api/login", json={"code": TEST_CODE})
        yield c
    server._backfill_analysis = orig
    remove_llm_config()


def wait_ai_done(client, item_id, timeout=5.0):
    """轮询单条接口等后台分析线程完成"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        it = client.get(f"/api/items/{item_id}").json()
        if it["ai_status"] in ("done", "failed"):
            return it
        time.sleep(0.05)
    raise AssertionError("后台分析超时未完成")


# ---------- 配置加载 ----------

def test_config_missing_file():
    remove_llm_config()
    assert llm.load_config() is None
    assert llm.is_configured() is False
    assert llm.status() == {"configured": False, "label": None, "model": None}


def test_config_bad_active():
    remove_llm_config()
    write_llm_config(active="nobody")
    assert llm.load_config() is None


def test_config_empty_key_treated_as_off():
    remove_llm_config()
    write_llm_config(active="empty_key")
    assert llm.load_config() is None
    assert llm.is_configured() is False


def test_config_valid():
    remove_llm_config()
    write_llm_config()
    cfg = llm.load_config()
    assert cfg["model"] == "fake-chat"
    assert cfg["base_url"] == "http://localhost:1/v1"
    assert cfg["label"] == "FakeLLM"
    assert cfg["vision"] is False          # 未标记 vision 的文本模型
    st = llm.status()
    assert st == {"configured": True, "label": "FakeLLM", "model": "fake-chat"}
    assert "api_key" not in st          # 状态接口绝不泄露 key


def test_config_vision_channel():
    """看图模型独立配置通道：vision_active 指向 vision:true 的供应商"""
    remove_llm_config()
    write_llm_config(vision=True)
    assert llm.is_configured("vision_active") is True
    vst = llm.status("vision_active")
    assert vst == {"configured": True, "label": "FakeVision", "model": "fake-vl"}
    assert llm.load_config()["model"] == "fake-chat"   # 文本通道不受影响
    # 未配置看图模型时 vision_active 通道返回未配置
    write_llm_config()
    assert llm.is_configured("vision_active") is False


def test_config_corrupt_file():
    remove_llm_config()
    llm.LLM_FILE.write_text("{ 不是 JSON", encoding="utf-8")
    assert llm.load_config() is None


def test_config_lenient_dangling_comma():
    """手改配置常见笔误（悬空逗号）应被兼容，而不是静默关闭功能"""
    remove_llm_config()
    llm.LLM_FILE.write_text(
        '{"active":"p1","providers":{"p1":{"label":"P1","base_url":"http://x/v1",'
        '"api_key":"k","model":"m",}}}', encoding="utf-8")
    cfg = llm.load_config()
    assert cfg and cfg["model"] == "m" and cfg["label"] == "P1"
    # 悬空逗号嵌在值字符串内时不能误删（正则只匹配 } ] 前的逗号）
    llm.LLM_FILE.write_text(
        '{"active":"p1","providers":{"p1":{"label":"a,b","base_url":"http://x/v1",'
        '"api_key":"k","model":"m"}}}', encoding="utf-8")
    assert llm.load_config()["label"] == "a,b"
    remove_llm_config()


# ---------- 提示词与解析 ----------

def test_build_prompt_photo_item():
    msgs = llm.build_prompt({
        "subject": "math", "item_type": "photo", "kp_name": "应用题",
        "reason_label": "审题马虎", "wrong_answer": "96 写成 47",
        "solution": "先算 12×8=96，再算 96-49=47",
    }, term="三年级上")
    assert msgs[0]["role"] == "system"
    text = msgs[1]["content"]
    assert "科目：数学" in text and "学期：三年级上" in text
    assert "应用题" in text and "审题马虎" in text
    assert "拍照题" in text          # 照片题必须说明"原文在照片中"
    assert "96 写成 47" in text and "96-49=47" in text


def test_build_prompt_uses_ocr_question_text():
    """有 OCR 题干的照片题：题干进入提示词，不再声明无法提供"""
    msgs = llm.build_prompt({
        "subject": "math", "item_type": "photo",
        "question_text": "12×8-49=？（种树应用题）",
        "wrong_answer": "算错", "solution": "先乘后减",
    })
    text = msgs[1]["content"]
    assert "题目原文：12×8-49=？（种树应用题）" in text
    assert "此处无法提供" not in text


def test_build_prompt_word_item():
    text = llm.build_prompt({
        "subject": "english", "item_type": "word", "prompt": "星期三",
        "wrong_text": "Wensday", "correct_text": "Wednesday",
    })[1]["content"]
    assert "科目：英语" in text and "看提示写字词" in text
    assert "Wensday" in text and "Wednesday" in text
    assert "拍照题" not in text


def test_parse_analysis_plain_and_wrapped():
    res = llm.parse_analysis('{"points": "两位数乘法", "pitfalls": "口诀不熟"}')
    assert res["points"] == "两位数乘法" and res["pitfalls"] == "口诀不熟"
    assert res["hints"] == [] and res["reason"] is None and res["kp"] is None
    wrapped = "好的，分析如下：\n```json\n{\"points\": \"时态\", \"pitfalls\": \"三单形式\"}\n```"
    assert llm.parse_analysis(wrapped)["points"] == "时态"


def test_parse_analysis_extended_fields():
    """P0 扩展字段：hints 最多两档，reason 必须合法四分类，kp 截断"""
    res = llm.parse_analysis(
        '{"points": "考点x", "pitfalls": "易错y", '
        '"hints": ["先想运算顺序", "括号先算", "多余的第三档"], '
        '"reason": "careless", "kp": "混合运算"}')
    assert res["hints"] == ["先想运算顺序", "括号先算"]
    assert res["reason"] == "careless"
    assert res["kp"] == "混合运算"
    # 非法错因归 None；超长 kp 截断
    res2 = llm.parse_analysis(
        '{"points": "a", "pitfalls": "b", "reason": "nonsense", '
        '"kp": "' + "长" * 99 + '", "hints": ["", "  "]}')
    assert res2["reason"] is None
    assert len(res2["kp"]) == llm.KP_MAX
    assert res2["hints"] == []                  # 空白提示被过滤


def test_parse_analysis_bad_inputs():
    with pytest.raises(RuntimeError):
        llm.parse_analysis("我不会输出 JSON")
    with pytest.raises(RuntimeError):
        llm.parse_analysis('{"points": "", "pitfalls": ""}')   # 两者都空 → 无效
    with pytest.raises(RuntimeError):
        llm.parse_analysis("{bad json")


def test_parse_analysis_truncates():
    res = llm.parse_analysis('{"points": "%s", "pitfalls": "%s"}' % ("长" * 99, "错" * 999))
    assert len(res["points"]) == llm.POINT_MAX
    assert len(res["pitfalls"]) == llm.PITFALL_MAX


# ---------- chat()（mock HTTP）----------

def test_chat_requires_config():
    remove_llm_config()
    with pytest.raises(RuntimeError):
        llm.chat([{"role": "user", "content": "hi"}])


def test_chat_ok(monkeypatch):
    write_llm_config()

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "模型回复"}}]}).encode()

    class FakeOpener:
        def __init__(self, cfg):
            FakeOpener.last_cfg = cfg
        def open(self, req, timeout=None):
            FakeOpener.last_url = req.full_url
            FakeOpener.last_headers = dict(req.headers)
            return FakeResp()

    monkeypatch.setattr(llm, "_opener", FakeOpener)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "模型回复"
    assert FakeOpener.last_url == "http://localhost:1/v1/chat/completions"
    assert FakeOpener.last_headers.get("Authorization") == "Bearer sk-fake"


def test_config_proxy_field():
    """供应商可选 proxy 字段：留空直连；配置则透传"""
    remove_llm_config()
    llm.LLM_FILE.write_text(json.dumps({
        "active": "p1",
        "providers": {"p1": {"label": "P1", "base_url": "http://x/v1",
                             "api_key": "k", "model": "m",
                             "proxy": "http://127.0.0.1:7890"}},
    }), encoding="utf-8")
    assert llm.load_config()["proxy"] == "http://127.0.0.1:7890"
    assert llm._opener(llm.load_config())  # 可构建 opener 不报错
    write_llm_config()
    assert llm.load_config()["proxy"] is None


def test_chat_extra_body_passthrough(monkeypatch):
    """供应商特有参数（如硅基流动 Qwen3 的 enable_thinking）透传到请求体"""
    remove_llm_config()
    llm.LLM_FILE.write_text(json.dumps({
        "active": "p1",
        "providers": {"p1": {"label": "P1", "base_url": "http://x/v1", "api_key": "k",
                             "model": "m", "extra_body": {"enable_thinking": False}}},
    }), encoding="utf-8")

    class FakeResp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()

    captured = {}

    class FakeOpener:
        def __init__(self, cfg):
            pass
        def open(self, req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return FakeResp()

    monkeypatch.setattr(llm, "_opener", FakeOpener)
    llm.chat([{"role": "user", "content": "hi"}])
    assert captured["body"]["enable_thinking"] is False
    assert captured["body"]["model"] == "m"
    assert captured["body"]["max_tokens"] == 600
    remove_llm_config()


# ---------- API：录入自动分析全链路 ----------

def test_create_without_llm_config(client):
    """未配置模型：录入不受影响，ai 字段保持空"""
    r = client.post("/api/items", json={
        "subject": "chinese", "prompt": "zhì（治理）",
        "wrong_text": "志", "correct_text": "治"})
    assert r.status_code == 200
    assert r.json()["ai_status"] is None
    assert client.get("/api/llm/status").json()["configured"] is False


def test_create_triggers_background_analysis(client, monkeypatch):
    """配置后录入 → 后台自动分析 → 结果落库可查"""
    write_llm_config()
    monkeypatch.setattr(llm, "chat", lambda messages, cfg=None:
                        '{"points": "形近字辨析", "pitfalls": "志/治 音同形近，需结合词义"}')
    r = client.post("/api/items", json={
        "subject": "chinese", "prompt": "zhì（治理）",
        "wrong_text": "志", "correct_text": "治"})
    assert r.status_code == 200
    assert r.json()["ai_status"] == "pending"          # 响应即带回分析中状态

    it = wait_ai_done(client, r.json()["id"])
    assert it["ai_status"] == "done"
    assert it["ai_points"] == "形近字辨析"
    assert "志/治" in it["ai_pitfalls"]
    assert it["ai_model"] == "fake-chat"
    assert it["ai_error"] is None

    st = client.get("/api/llm/status").json()
    assert st["configured"] is True and st["model"] == "fake-chat"
    assert st["items"].get("done", 0) >= 1


def test_analysis_failure_recorded(client, monkeypatch):
    """模型调用失败 → 状态 failed + 错误信息，不影响主流程"""
    write_llm_config()

    def boom(messages, cfg=None):
        raise RuntimeError("接口返回 401：无效 key")

    monkeypatch.setattr(llm, "chat", boom)
    r = client.post("/api/items", json={
        "subject": "english", "prompt": "星期四",
        "wrong_text": "Thuseday", "correct_text": "Thursday"})
    it = wait_ai_done(client, r.json()["id"])
    assert it["ai_status"] == "failed"
    assert "401" in it["ai_error"]
    # 之后修好模型，可手动重新分析成功
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"points": "单词拼写", "pitfalls": "Thursday 记错中间音节"}')
    assert client.post(f"/api/items/{it['id']}/analyze").status_code == 200
    fixed = wait_ai_done(client, it["id"])
    assert fixed["ai_status"] == "done" and fixed["ai_points"] == "单词拼写"


def test_manual_analyze_errors(client, monkeypatch):
    iid = client.post("/api/items", json={
        "subject": "chinese", "prompt": "p", "wrong_text": "x", "correct_text": "y"}).json()["id"]
    # 未配置 → 400 明确提示
    remove_llm_config()
    assert client.post(f"/api/items/{iid}/analyze").status_code == 400
    assert client.post("/api/items/99999/analyze").status_code == 404
    # 配置后可手动触发
    write_llm_config()
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"points": "考点", "pitfalls": "易错"}')
    assert client.post(f"/api/items/{iid}/analyze").status_code == 200
    assert wait_ai_done(client, iid)["ai_status"] == "done"


def test_get_single_item_404(client):
    assert client.get("/api/items/99999").status_code == 404


def test_items_columns_migrated(client):
    """老库升级：migrate 后 items 表应包含全部 AI 列"""
    conn = db.get_conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    conn.close()
    assert {"ai_points", "ai_pitfalls", "ai_status", "ai_model", "ai_error"} <= cols


# ---------- 启动补分析 ----------

def test_backfill_only_null_items(monkeypatch):
    """只补 ai_status 为空的题；done/failed 不重复分析"""
    conn = db.get_conn()
    now = db.now_str()
    ids = []
    for i in range(3):
        cur = conn.execute(
            "INSERT INTO items(subject, item_type, reason, prompt, wrong_text, correct_text,"
            " stage, due_date, status, term, created_at) VALUES(?,?,?,?,?,?,0,?, 'reviewing',"
            " '三年级上', ?)",
            ("chinese", "word", "concept", f"p{i}", "x", "y",
             engine.due_after_create(engine.today()), now))
        ids.append(cur.lastrowid)
    # 第 2 题标记 done、第 3 题标记 failed → 都不该被重跑
    conn.execute("UPDATE items SET ai_status='done', ai_points='旧' WHERE id=?", (ids[1],))
    conn.execute("UPDATE items SET ai_status='failed', ai_error='x' WHERE id=?", (ids[2],))
    conn.commit()
    conn.close()

    write_llm_config()
    calls = []

    def fake_chat(messages, cfg=None):
        calls.append(messages[1]["content"])
        return '{"points": "新分析", "pitfalls": "新易错"}'

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    server._backfill_analysis()

    # 核心断言：为空的题被补分析；done/failed 的题保持原样、不重复扣费
    assert len(calls) >= 1
    conn = db.get_conn()
    rows = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, ai_status, ai_points, ai_error FROM items WHERE id IN (?,?,?)", ids)}
    conn.close()
    assert rows[ids[0]]["ai_status"] == "done"
    assert rows[ids[0]]["ai_points"] == "新分析"
    assert rows[ids[1]]["ai_status"] == "done" and rows[ids[1]]["ai_points"] == "旧"
    assert rows[ids[2]]["ai_status"] == "failed" and rows[ids[2]]["ai_error"] == "x"
    # 刚标记 done/failed 的两道题不应出现在任何一次分析请求里
    assert all("提示：p1" not in c for c in calls)
    assert all("提示：p2" not in c for c in calls)


def test_backfill_skips_when_off(monkeypatch):
    remove_llm_config()
    monkeypatch.setattr(server.llm, "is_configured", lambda: False)
    server._backfill_analysis()                  # 未配置：静默返回，不抛错


# ---------- P0：懒生成提示 ----------

def test_generate_hints(monkeypatch):
    write_llm_config()
    monkeypatch.setattr(llm, "chat",
                        lambda m, cfg=None: '{"hints": ["先想想运算顺序", "括号里先算"]}')
    hints = llm.generate_hints({"subject": "math", "item_type": "photo",
                                "question_text": "12×8-49=?", "wrong_answer": "x",
                                "solution": "先乘后减"})
    assert hints == ["先想想运算顺序", "括号里先算"]
    # word 题走提示项分支
    hints2 = llm.generate_hints({"subject": "chinese", "item_type": "word",
                                 "prompt": "zhì", "wrong_text": "志"})
    assert hints2 == ["先想想运算顺序", "括号里先算"]


def test_generate_hints_requires_config_and_json(monkeypatch):
    remove_llm_config()
    with pytest.raises(RuntimeError):
        llm.generate_hints({"subject": "math"})
    write_llm_config()
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None: "我给不了 JSON")
    with pytest.raises(RuntimeError):
        llm.generate_hints({"subject": "math", "item_type": "word", "prompt": "p"})


def test_hints_endpoint_generate_and_cache(client, monkeypatch):
    """首点现场生成并落库，再点走缓存不重复调用模型"""
    iid = client.post("/api/items", json={
        "subject": "chinese", "prompt": "p", "wrong_text": "x", "correct_text": "y"}).json()["id"]
    write_llm_config()          # 录入后再配置，避免触发真实分析线程
    calls = []

    def fake_gen(item, term=""):
        calls.append(1)
        return ["提示A", "提示B"]

    monkeypatch.setattr(llm, "generate_hints", fake_gen)
    r = client.post(f"/api/items/{iid}/hints")
    assert r.status_code == 200
    assert r.json() == {"hints": ["提示A", "提示B"], "cached": False}
    r2 = client.post(f"/api/items/{iid}/hints")
    assert r2.json()["cached"] is True and len(calls) == 1
    remove_llm_config()


def test_hints_endpoint_guards(client):
    """未配置 400；设置关闭 403；不存在的题 404；已缓存提示无需模型"""
    iid = client.post("/api/items", json={
        "subject": "english", "prompt": "p", "wrong_text": "x", "correct_text": "y"}).json()["id"]
    remove_llm_config()
    assert client.post(f"/api/items/{iid}/hints").status_code == 400
    assert client.post("/api/items/99999/hints").status_code == 404
    # 关闭开关后即便配置了模型也 403
    write_llm_config()
    client.put("/api/settings", json={"ai_hint_enabled": False})
    assert client.post(f"/api/items/{iid}/hints").status_code == 403
    client.put("/api/settings", json={"ai_hint_enabled": True})
    remove_llm_config()


def test_ai_hint_setting_roundtrip(client):
    meta = client.get("/api/meta").json()
    assert meta["settings"]["ai_hint_enabled"] is True       # 默认开启
    r = client.put("/api/settings", json={"ai_hint_enabled": False}).json()
    assert r["ai_hint_enabled"] is False
    assert client.get("/api/meta").json()["settings"]["ai_hint_enabled"] is False
    client.put("/api/settings", json={"ai_hint_enabled": True})


# ---------- P0：AI 归因建议采用 ----------

def test_patch_kp_name_match_and_create(client):
    """按名称匹配既有知识点；不存在则新建（AI 建议采用路径）"""
    iid = client.post("/api/items", json={
        "subject": "chinese", "prompt": "p", "wrong_text": "x", "correct_text": "y"}).json()["id"]
    r = client.patch(f"/api/items/{iid}", json={"kp_name": "形近字"}).json()
    assert r["kp_name"] == "形近字"
    r2 = client.patch(f"/api/items/{iid}", json={"kp_name": "超稀有自定义知识点"}).json()
    assert r2["kp_name"] == "超稀有自定义知识点"
    # 清空则置 NULL
    r3 = client.patch(f"/api/items/{iid}", json={"kp_name": ""}).json()
    assert r3["kp_name"] is None and r3["kp_id"] is None


def test_analysis_stores_suggestions(client, monkeypatch):
    """分析结果中的归因建议/提示落库，可在编辑弹窗被采用"""
    write_llm_config()
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"points": "形近字", "pitfalls": "同音混淆", '
                        '"hints": ["想偏旁", "想词义"], "reason": "concept", "kp": "形近字"}')
    r = client.post("/api/items", json={
        "subject": "chinese", "prompt": "zhì", "wrong_text": "志", "correct_text": "治"})
    it = wait_ai_done(client, r.json()["id"])
    assert it["ai_reason_suggest"] == "concept"
    assert it["ai_kp_suggest"] == "形近字"
    assert json.loads(it["ai_hints"]) == ["想偏旁", "想词义"]
    # 建议 ≠ 当前值，采用后生效
    assert it["reason"] != "concept"
    adopted = client.patch(f"/api/items/{it['id']}",
                           json={"reason": it["ai_reason_suggest"]}).json()
    assert adopted["reason"] == "concept"
    remove_llm_config()


# ---------- P0：看图模型 OCR ----------

def test_extract_question(monkeypatch):
    cfg = {"base_url": "http://x", "api_key": "k", "model": "m", "vision": True,
           "max_tokens": 10, "temperature": 0.1}
    monkeypatch.setattr(llm, "chat", lambda m, c=None: "  12 × 8 - 49 = ?  ")
    assert llm.extract_question("abc", cfg) == "12 × 8 - 49 = ?"
    with pytest.raises(RuntimeError):        # 未标记 vision 能力的模型不可用
        llm.extract_question("abc", {**cfg, "vision": False})


def test_analysis_ocr_flow(client, monkeypatch, tmp_path):
    """照片题分析前先 OCR 题干；题干进入分析提示词；OCR 失败不影响分析"""
    from PIL import Image
    write_llm_config(vision=True)
    png = tmp_path / "q.jpg"
    Image.new("RGB", (10, 10), "#FFF").save(png)
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO items(subject, item_type, stage, due_date, status, term, created_at)"
        " VALUES('math','photo',0,'2020-01-01','paused','三年级上','2026-01-01 10:00:00')")
    iid = cur.lastrowid
    conn.execute("INSERT INTO photos(item_id, orig_path, thumb_path, created_at)"
                 " VALUES(?,?,?,?)", (iid, str(png), str(png), "2026-01-01 10:00:00"))
    conn.commit()
    conn.close()

    seen = {}
    monkeypatch.setattr(llm, "extract_question",
                        lambda b64, cfg=None: "每组12人，8个组共多少人？")
    monkeypatch.setattr(llm, "chat", lambda messages, cfg=None: (
        seen.update(prompt=messages[1]["content"]) or
        '{"points": "乘法应用", "pitfalls": "别数错", "reason": "careless", "kp": "应用题"}'))
    server._claim(iid)
    server._run_analysis(iid)

    conn = db.get_conn()
    row = conn.execute("SELECT question_text, ai_status, ai_points FROM items WHERE id=?",
                       (iid,)).fetchone()
    conn.close()
    assert row["question_text"] == "每组12人，8个组共多少人？"
    assert row["ai_status"] == "done" and row["ai_points"] == "乘法应用"
    assert "题目原文：每组12人" in seen["prompt"]
    remove_llm_config()


def test_ocr_skipped_without_vision(client, monkeypatch, tmp_path):
    """未配置看图模型：照片题直接走文字推断，question_text 保持空"""
    from PIL import Image
    write_llm_config()          # 仅文本模型
    png = tmp_path / "q2.jpg"
    Image.new("RGB", (10, 10), "#FFF").save(png)
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO items(subject, item_type, stage, due_date, status, term, created_at)"
        " VALUES('math','photo',0,'2020-01-01','paused','三年级上','2026-01-01 10:00:00')")
    iid = cur.lastrowid
    conn.execute("INSERT INTO photos(item_id, orig_path, thumb_path, created_at)"
                 " VALUES(?,?,?,?)", (iid, str(png), str(png), "2026-01-01 10:00:00"))
    conn.commit()
    conn.close()

    def fail_ocr(*a, **k):
        raise AssertionError("不应调用 OCR")

    monkeypatch.setattr(llm, "extract_question", fail_ocr)
    monkeypatch.setattr(llm, "chat", lambda m, cfg=None:
                        '{"points": "推断考点", "pitfalls": "x"}')
    server._claim(iid)
    server._run_analysis(iid)
    conn = db.get_conn()
    row = conn.execute("SELECT question_text, ai_status, ai_points FROM items WHERE id=?",
                       (iid,)).fetchone()
    conn.close()
    assert row["question_text"] is None
    assert row["ai_status"] == "done" and row["ai_points"] == "推断考点"
    remove_llm_config()


def test_llm_status_includes_vision(client):
    write_llm_config(vision=True)
    st = client.get("/api/llm/status").json()
    assert st["configured"] is True
    assert st["vision"]["configured"] is True
    assert st["vision"]["model"] == "fake-vl"
    assert "api_key" not in json.dumps(st)     # 双通道都不泄露 key
    remove_llm_config()


def test_stats_pending_includes_photo_and_question(client, tmp_path):
    """待复核列表必须带照片 ID 与 OCR 题干，家长才能复核照片题（修复"看不到原题"）"""
    from PIL import Image
    png = tmp_path / "pv.jpg"
    Image.new("RGB", (10, 10), "#FFF").save(png)
    now = db.now_str()
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO items(subject, item_type, question_text, wrong_answer, solution,"
        " stage, due_date, status, term, created_at)"
        " VALUES('math','photo','6 箱苹果每箱 24 个，还剩多少？','抄错数','先乘后减',"
        " 1,'2020-01-01','reviewing','三年级上',?)", (now,))
    iid = cur.lastrowid
    pid = conn.execute("INSERT INTO photos(item_id, orig_path, thumb_path, created_at)"
                       " VALUES(?,?,?,?)", (iid, str(png), str(png), now)).lastrowid
    conn.execute("INSERT INTO review_log(item_id, result, judged_by, pending_verify, created_at)"
                 " VALUES(?,?,?,1,?)", (iid, "pass", "child", now))
    conn.commit()
    conn.close()

    st = client.get("/api/stats").json()
    p = next(x for x in st["pending"] if x["item_id"] == iid)
    assert p["photo_ids"] == [pid]
    assert "6 箱苹果" in p["question_text"]
    assert p["wrong_answer"] == "抄错数" and p["solution"] == "先乘后减"
