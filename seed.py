"""演示数据（可选）：python seed.py --demo

仅在空库时写入一组样例错题，便于全家先体验完整流程；
正式使用时无需运行，或清空 data/ 目录后重新初始化。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import db
import engine
from config import PHOTO_DIR, THUMB_DIR

FONT = Path(__file__).parent / "fonts" / "NotoSansSC-Regular.ttf"


def make_demo_photo(lines: list[str], out_path: Path):
    W, H = 1000, 60 + 80 * len(lines)
    im = Image.new("RGB", (W, H), "#FFFFFF")
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(FONT), 34)
    for i, ln in enumerate(lines):
        d.text((50, 40 + i * 80), ln, font=font, fill="#111111")
    d.rectangle([2, 2, W - 3, H - 3], outline="#CCCCCC", width=3)
    im.save(out_path, "JPEG", quality=88)
    t = im.copy()
    t.thumbnail((360, 360))
    thumb = THUMB_DIR / out_path.name
    t.save(thumb, "JPEG", quality=75)


def demo():
    db.init_db()
    conn = db.get_conn()
    if conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] > 0:
        print("库中已有错题，跳过演示数据（如需重来请先清空 data/ 目录）")
        conn.close()
        return

    term = db.settings_get(conn, "current_term")
    today_iso = engine.today().isoformat()
    tomorrow_iso = engine.add_days(engine.today(), 1).isoformat()

    def back(days: int) -> str:
        return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    def kp(subject, name):
        return conn.execute("SELECT id FROM knowledge_points WHERE subject=? AND name=?",
                            (subject, name)).fetchone()["id"]

    def add(subject, item_type, kp_id, reason, *, stage=0, due=today_iso, wc=0,
            status="reviewing", created_days_ago=1, prompt=None, wrong=None,
            correct=None, wrong_answer=None, solution=None, mastered_at=None):
        cur = conn.execute(
            "INSERT INTO items(subject, item_type, kp_id, reason, prompt, wrong_text, "
            "correct_text, wrong_answer, solution, stage, due_date, wrong_count, status, "
            "term, created_at, mastered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (subject, item_type, kp_id, reason, prompt, wrong, correct, wrong_answer,
             solution, stage, due, wc, status, term, back(created_days_ago), mastered_at))
        return cur.lastrowid

    def attach_photo(item_id, stem):
        orig, thumb = PHOTO_DIR / f"{stem}.jpg", THUMB_DIR / f"{stem}.jpg"
        conn.execute("INSERT INTO photos(item_id, orig_path, thumb_path, created_at) "
                     "VALUES(?,?,?,?)", (item_id, str(orig), str(thumb), db.now_str()))

    # 1) 数学 · 照片题（第 2 档，今日到期）
    i1 = add("math", "photo", kp("math", "应用题"), "careless", stage=2, created_days_ago=4,
             wrong_answer="最后一步把 96 写成 47",
             solution="先算 12×8=96，再算 96-49=47；注意先乘除后加减")
    make_demo_photo(["三（2）班同学去种树，每组 12 人，", "8 个组一共多少人？", "",
                     "种完后发现枯了 49 棵，还剩多少棵？", "",
                     "小明列式：12×8-49=47（棵）"], PHOTO_DIR / f"{i1}_demo.jpg")
    attach_photo(i1, f"{i1}_demo")

    # 2) 语文 · 错字词（第 1 档，今日到期）
    add("chinese", "word", kp("chinese", "形近字"), "concept",
        stage=1, prompt="zhì（治理）", wrong="志", correct="治")

    # 3) 英语 · 错词（第 2 档，今日到期）
    add("english", "word", kp("english", "单词拼写"), "careless",
        stage=2, prompt="星期三", wrong="Wensday", correct="Wednesday")

    # 4) 英语 · 顽固错词（错 3 次，今日到期，清单置顶）
    add("english", "word", kp("english", "单词拼写"), "clueless",
        stage=0, wc=3, prompt="星期四", wrong="Thuseday", correct="Thursday")

    # 5) 语文 · 错词（明日到期，展示排期）
    add("chinese", "word", kp("chinese", "看拼音写汉字"), "careless",
        stage=1, due=tomorrow_iso, prompt="chéng jì（成绩）", wrong="成积", correct="成绩")

    # 6) 数学 · 已消灭题（光荣榜示例）
    i6 = add("math", "photo", kp("math", "混合运算"), "calc", stage=5, due=None,
             status="mastered", wc=1, created_days_ago=40, mastered_at=back(3),
             solution="先算括号 25+15=40，再算 40÷8=5")
    make_demo_photo(["计算：（25 + 15）÷ 8 =", "", "小马虎直接算了 25+15÷8"], PHOTO_DIR / f"{i6}_demo.jpg")
    attach_photo(i6, f"{i6}_demo")

    conn.commit()
    n = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
    conn.close()
    print(f"演示数据已写入：{n} 道错题（含 1 道顽固题、1 道已消灭题、1 道明日到期）")
    print("打开「今日」页即可体验完整复习流程。")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        print("用法: python seed.py --demo")
