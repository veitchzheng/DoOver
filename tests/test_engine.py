"""复习引擎纯逻辑单元测试"""
from datetime import date, timedelta

from engine import MASTER_STAGE, add_days, compute_judgment, due_after_create, is_stubborn

T = date(2026, 11, 6)


def mk(stage=0, wrong_count=0, status="reviewing", created="2026-11-05 20:00:00", due="2026-11-06"):
    return {"stage": stage, "wrong_count": wrong_count, "status": status,
            "created_at": created, "due_date": due}


def test_due_after_create():
    assert due_after_create(date(2026, 11, 6)) == "2026-11-07"
    assert due_after_create(date(2026, 12, 31)) == "2027-01-01"  # 跨年


def test_pass_first_stage():
    upd = compute_judgment(mk(), "pass", T)
    assert upd["stage"] == 1
    assert upd["due_date"] == "2026-11-08"  # 创建日 11-05 + 第 1 档偏移 3 天
    assert upd["stars"] == [("pass", 1)]
    assert not upd["mastered"]


def test_pass_clamp_when_late():
    """复习拖堂：下次复习日不得早于明天"""
    item = mk(stage=2, created="2026-10-01 20:00:00")  # 第 3 档本应 10-15 考
    upd = compute_judgment(item, "pass", T)
    assert upd["stage"] == 3
    assert upd["due_date"] == (T + timedelta(days=1)).isoformat()  # 顺延到明天


def test_pass_master():
    upd = compute_judgment(mk(stage=4), "pass", T)
    assert upd["mastered"] and upd["stage"] == MASTER_STAGE
    assert upd["status"] == "mastered" and upd["due_date"] is None
    assert upd["stars"] == [("master", 5)]


def test_pass_master_stubborn_bonus():
    upd = compute_judgment(mk(stage=4, wrong_count=3), "pass", T)
    assert upd["stars"] == [("stubborn_master", 10)]


def test_hint_keeps_stage():
    upd = compute_judgment(mk(stage=2), "hint", T)
    assert upd["stage"] == 2
    assert upd["due_date"] == "2026-11-07"
    assert upd["stars"] == []


def test_fail_resets():
    upd = compute_judgment(mk(stage=3, wrong_count=1), "fail", T)
    assert upd["stage"] == 0
    assert upd["wrong_count"] == 2
    assert upd["due_date"] == "2026-11-07"


def test_invalid_result():
    try:
        compute_judgment(mk(), "what", T)
        assert False, "应抛出 ValueError"
    except ValueError:
        pass


def test_is_stubborn():
    assert is_stubborn({"wrong_count": 3, "status": "reviewing"})
    assert not is_stubborn({"wrong_count": 3, "status": "mastered"})
    assert not is_stubborn({"wrong_count": 2, "status": "reviewing"})


def test_add_days():
    assert add_days(date(2026, 11, 6), 3) == date(2026, 11, 9)
    assert add_days(date(2026, 11, 6), -1) == date(2026, 11, 5)
