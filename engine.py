"""复习引擎：排期、三键判定、每日清单、连击、星星与勋章

判定规则（与设计文档一致）：
  ✅ pass  独立做对：阶段 +1；通过第 5 档 → 消灭（+5 星；错次 ≥3 的顽固题 +10 星）
  ⚠️ hint  提示后才对：阶段不变，次日再考
  ❌ fail  又错：归零回 1 天档，错次 +1；错次 ≥3 视为顽固题（清单置顶、统计高亮）
  当日全部到期题清空且全为 pass → 额外 +2 星（每日一次）
"""
from datetime import date, timedelta

from config import INTERVAL_OFFSETS, MASTER_STAGE


def today() -> date:
    return date.today()


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def item_date(item) -> date:
    """item['created_at'] 形如 'YYYY-MM-DD HH:MM:SS' → date"""
    return date.fromisoformat(str(item["created_at"])[:10])


def due_after_create(created: date) -> str:
    """新题录入后：次日进入第 1 档复习"""
    return (created + timedelta(days=INTERVAL_OFFSETS[0])).isoformat()


def compute_judgment(item, result: str, today_d: date) -> dict:
    """纯函数：给定当前题与判定，返回新状态与星星事件。不做任何 IO。

    返回：{stage, due_date, status, wrong_count, mastered, stars: [(event, num), ...]}
    """
    stars = []
    stage = item["stage"]
    wrong_count = item["wrong_count"]
    status = item["status"]
    due = item["due_date"]
    mastered = False

    if result == "pass":
        stage += 1
        if stage >= MASTER_STAGE:  # 消灭
            mastered = True
            status = "mastered"
            due = None
            stars.append(("stubborn_master" if wrong_count >= 3 else "master",
                          10 if wrong_count >= 3 else 5))
        else:
            # 下次复习日 = 创建日 + 第 N 档偏移；拖堂则顺延至明天
            due = max(item_date(item) + timedelta(days=INTERVAL_OFFSETS[stage]),
                      today_d + timedelta(days=1)).isoformat()
            status = "reviewing"
            stars.append(("pass", 1))
    elif result == "hint":
        stage = stage  # 阶段不变
        due = (today_d + timedelta(days=1)).isoformat()
        status = "reviewing"
    elif result == "fail":
        stage = 0
        wrong_count += 1
        due = (today_d + timedelta(days=1)).isoformat()
        status = "reviewing"
    else:
        raise ValueError(f"非法判定结果: {result}")

    return {"stage": stage, "due_date": due, "status": status,
            "wrong_count": wrong_count, "mastered": mastered, "stars": stars}


def is_stubborn(item) -> bool:
    return item["wrong_count"] >= 3 and item["status"] == "reviewing"


def get_today_list(conn, limit: int, today_iso: str):
    """今日到期清单：顽固题置顶，其次最早到期，限量（默认 8，防堆量）"""
    return conn.execute(
        "SELECT * FROM items WHERE status='reviewing' AND due_date <= ? "
        "ORDER BY (wrong_count >= 3) DESC, due_date ASC, created_at ASC LIMIT ?",
        (today_iso, limit)).fetchall()


def remaining_due(conn, today_iso: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM items WHERE status='reviewing' AND due_date <= ?",
        (today_iso,)).fetchone()["c"]


def should_award_day_bonus(conn, today_iso: str) -> bool:
    """当日加星条件：今天有 ≥1 次 pass、无 hint/fail、且到期题已清空（含补欠账）"""
    row = conn.execute(
        "SELECT SUM(result='pass') p, SUM(result!='pass') np FROM review_log "
        "WHERE substr(created_at,1,10)=?", (today_iso,)).fetchone()
    if not row or not row["p"] or row["np"]:
        return False
    if conn.execute("SELECT COUNT(*) c FROM star_ledger WHERE event='day_all' "
                    "AND substr(created_at,1,10)=?", (today_iso,)).fetchone()["c"]:
        return False  # 每日只发一次
    return remaining_due(conn, today_iso) == 0


def streak(conn, today_iso: str) -> int:
    """连续复习天数：从今天（或昨天）往前数有判定记录的连续天数"""
    dates = [r["d"] for r in conn.execute(
        "SELECT DISTINCT substr(created_at,1,10) d FROM review_log ORDER BY d DESC")]
    if not dates:
        return 0
    start = date.fromisoformat(dates[0])
    if (start - today()).days == -1:  # 今天还没复习，从昨天起算
        start = date.fromisoformat(dates[0])
    elif start != today():
        return 0
    n, expect = 0, start
    for d in dates:
        if date.fromisoformat(d) == expect:
            n += 1
            expect = add_days(expect, -1)
        else:
            break
    return n


def streak_badge_date(conn, n: int) -> str | None:
    """首次达成"连续 n 天复习"的日期（三天连击 n=3 / 坚持之星 n=7）"""
    dates = [r["d"] for r in conn.execute(
        "SELECT DISTINCT substr(created_at,1,10) d FROM review_log ORDER BY d")]
    cur_start, cur_len, prev = None, 0, None
    for d in dates:
        dd = date.fromisoformat(d)
        if prev is not None and (dd - prev).days == 1:
            cur_len += 1
        else:
            cur_start, cur_len = d, 1
        if cur_len >= n:
            return cur_start
        prev = dd
    return None


def total_stars(conn) -> int:
    """可用余额：全部流水求和（含兑换扣减与复核追回的负数项）"""
    return conn.execute("SELECT COALESCE(SUM(stars),0) s FROM star_ledger").fetchone()["s"]


def stars_earned(conn) -> int:
    """累计赚得：只算正向流水，兑换/追回不影响，是只增不减的荣誉值"""
    return conn.execute("SELECT COALESCE(SUM(stars),0) s FROM star_ledger WHERE stars > 0").fetchone()["s"]


def add_stars(conn, event: str, item_id, num: int, now_iso: str):
    conn.execute("INSERT INTO star_ledger(event, item_id, stars, created_at) VALUES(?,?,?,?)",
                 (event, item_id, num, now_iso))


def compute_badges(conn) -> list[dict]:
    """勋章由日志与题目推算，不落库。返回带 unlocked/achieved_at 的完整勋章墙。
    前 3 枚短周期（当天/3 天可点亮），避免新用户勋章墙长期全灰挫伤积极性。"""
    badges = []

    def first_review():
        r = conn.execute("SELECT MIN(substr(created_at,1,10)) d FROM review_log").fetchone()
        return r["d"] if r and r["d"] else None

    def first_day_clear():
        r = conn.execute("SELECT MIN(substr(created_at,1,10)) d FROM star_ledger "
                         "WHERE event='day_all'").fetchone()
        return r["d"] if r and r["d"] else None

    def first_master():
        r = conn.execute("SELECT MIN(mastered_at) m FROM items WHERE status='mastered'").fetchone()
        return r["m"][:10] if r and r["m"] else None

    def mastered_count_at(n):
        """第 n 道消灭题的消灭日期"""
        r = conn.execute("SELECT mastered_at m FROM items WHERE status='mastered' "
                         "AND mastered_at IS NOT NULL ORDER BY mastered_at LIMIT 1 OFFSET ?",
                         (n - 1,)).fetchone()
        return r["m"][:10] if r else None

    def comeback():
        r = conn.execute("SELECT MIN(mastered_at) m FROM items WHERE status='mastered' "
                         "AND wrong_count >= 3").fetchone()
        return r["m"][:10] if r and r["m"] else None

    def trivium():
        dates = []
        for subj in ("math", "chinese", "english"):
            d = conn.execute("SELECT mastered_at m FROM items WHERE status='mastered' "
                             "AND subject=? AND mastered_at IS NOT NULL "
                             "ORDER BY mastered_at LIMIT 1 OFFSET 9", (subj,)).fetchone()
            if not d or not d["m"]:
                return None
            dates.append(d["m"][:10])
        return max(dates)

    checks = [
        ("first_review", "首战告捷", "完成第一次复习判定", first_review),
        ("day_clear", "今日通关", "首次当日任务全部通过", first_day_clear),
        ("streak3", "三天连击", "连续复习 3 天", lambda: streak_badge_date(conn, 3)),
        ("first_master", "初次胜利", "消灭第 1 道错题", first_master),
        ("streak7", "坚持之星", "连续复习 7 天", lambda: streak_badge_date(conn, 7)),
        ("hunter10", "怪兽猎人", "累计消灭 10 道错题", lambda: mastered_count_at(10)),
        ("comeback", "翻身仗", "顽固题（错 3 次以上）逆袭消灭", comeback),
        ("slayer50", "怪兽克星", "累计消灭 50 道错题", lambda: mastered_count_at(50)),
        ("trivium", "三科通吃", "数学/语文/英语各消灭 10 题", trivium),
    ]
    for key, name, desc, fn in checks:
        try:
            at = fn()
        except Exception:
            at = None
        badges.append({"key": key, "name": name, "desc": desc,
                       "unlocked": bool(at), "achieved_at": at})
    return badges
