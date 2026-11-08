"""PDF 导出：收藏本 / 重练卷 双模板（reportlab + 内嵌 Noto Sans SC）

- 收藏本：题 + 孩子答案 + 错因分析 + 正确做法 + 复习状态，用于归档翻阅
- 重练卷：只有题 + 演算留白，答案集中末页（可沿虚线撕下），用于纸质重练
- 语文错字词默写自动排田字格，英语错词排四线三格
- 题块不跨页（KeepTogether），页脚含页码与生成信息
"""
import html
import math
import random
from datetime import datetime
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, Image, KeepTogether,
                                PageBreak, PageTemplate, Paragraph, Spacer)

from config import APP_VERSION, EXPORT_DIR, ensure_dirs

FONT_DIR = Path(__file__).parent / "fonts"
FONT_R = "NotoSansSC"
FONT_B = "NotoSansSC-Bold"

C_PRIMARY = colors.HexColor("#0E6B57")
C_ACCENT = colors.HexColor("#14B8A6")
C_RED = colors.HexColor("#E5484D")
C_GREEN = colors.HexColor("#16A34A")
C_INK = colors.HexColor("#1F2D3D")
C_GRAY = colors.HexColor("#6B7280")
C_LINE = colors.HexColor("#D8DEE4")
C_BG = colors.HexColor("#EEF6F3")

M = 18 * mm  # 页边距
PAGE_W, PAGE_H = A4
CONTENT_W = PAGE_W - 2 * M

RESULT_MARK = {"pass": "√", "hint": "△", "fail": "×"}  # PDF 内不用 emoji，避免缺字形

_registered = False


def register_fonts():
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont(FONT_R, str(FONT_DIR / "NotoSansSC-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_B, str(FONT_DIR / "NotoSansSC-Bold.ttf")))
    pdfmetrics.registerFontFamily(FONT_R, normal=FONT_R, bold=FONT_B,
                                  italic=FONT_R, boldItalic=FONT_B)
    _registered = True


def esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


# ---------- 自定义 Flowable ----------

class TianZiGe(Flowable):
    """田字格（语文生字默写），chars 为需要默写的字数"""

    def __init__(self, chars: int, box=15 * mm, gap=2 * mm):
        super().__init__()
        self.n = max(1, int(chars))
        self.box, self.gap = box, gap
        self.per_row = 10
        self.rows = 1
        self.height = box

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        per = max(1, int(availWidth // (self.box + self.gap)))
        self.per_row = min(per, 10)
        self.rows = max(1, math.ceil(self.n / self.per_row))
        self.height = self.rows * self.box + (self.rows - 1) * self.gap + 2 * mm
        return availWidth, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        for i in range(self.n):
            r, col = divmod(i, self.per_row)
            x = col * (self.box + self.gap)
            y = self.height - 2 * mm - (r + 1) * self.box - r * self.gap
            c.setLineWidth(1.1)
            c.setStrokeColor(colors.HexColor("#444444"))
            c.rect(x, y, self.box, self.box, stroke=1, fill=0)
            c.setStrokeColor(colors.HexColor("#AAAAAA"))
            c.setDash(2, 2)
            c.line(x, y + self.box / 2, x + self.box, y + self.box / 2)
            c.line(x + self.box / 2, y, x + self.box / 2, y + self.box)
            c.setDash()
        c.restoreState()


class FourLineGrid(Flowable):
    """四线三格（英语单词默写）"""

    def __init__(self, rows: int = 1, gap: float = 3.4 * mm):
        super().__init__()
        self.rows = max(1, int(rows))
        self.gap = gap
        self.height = rows * 4 * gap + 3 * mm

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return availWidth, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        c.setLineWidth(0.9)
        c.setStrokeColor(colors.HexColor("#555555"))
        for r in range(self.rows):
            base = self.height - 2 * mm - r * 4 * self.gap
            for k in range(4):
                y = base - k * self.gap
                if k == 1:  # 第二线（上格线）虚一点
                    c.setStrokeColor(colors.HexColor("#999999"))
                else:
                    c.setStrokeColor(colors.HexColor("#555555"))
                c.line(0, y, self.width, y)
        c.restoreState()


class AnswerLines(Flowable):
    """演算留白：n 条浅灰横线"""

    def __init__(self, n: int = 10, gap: float = 9 * mm):
        super().__init__()
        self.n = max(1, int(n))
        self.gap = gap
        self.height = n * gap + 2 * mm

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return availWidth, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        c.setLineWidth(0.7)
        c.setStrokeColor(colors.HexColor("#C9D2D8"))
        for i in range(self.n):
            y = self.height - 2 * mm - (i + 1) * self.gap + self.gap * 0.35
            c.line(0, y, self.width, y)
        c.restoreState()


class TearLine(Flowable):
    """答案页顶部的"沿虚线撕下"分割线"""

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return availWidth, 9 * mm

    def draw(self):
        c = self.canv
        c.saveState()
        c.setDash(5, 3)
        c.setLineWidth(0.9)
        c.setStrokeColor(colors.HexColor("#888888"))
        c.line(0, 3 * mm, self.width, 3 * mm)
        c.setDash()
        c.setFont(FONT_R, 8.5)
        c.setFillColor(colors.HexColor("#888888"))
        c.drawCentredString(self.width / 2, 5 * mm, "—— 沿此线撕下 ——")
        c.restoreState()


# ---------- 样式 ----------

def _styles() -> dict:
    from reportlab.lib.enums import TA_CENTER
    return {
        "cover_title": ParagraphStyle("ct", fontName=FONT_B, fontSize=26, leading=36,
                                      textColor=C_PRIMARY, alignment=TA_CENTER, spaceAfter=6),
        "cover_sub": ParagraphStyle("cs", fontName=FONT_R, fontSize=13, leading=20,
                                    textColor=C_GRAY, alignment=TA_CENTER),
        "cover_line": ParagraphStyle("cl", fontName=FONT_R, fontSize=12.5, leading=24,
                                    textColor=C_INK),
        "group": ParagraphStyle("g", fontName=FONT_B, fontSize=13.5, leading=18,
                                textColor=C_PRIMARY, backColor=C_BG, borderPadding=(5, 8, 5, 8),
                                spaceBefore=10, spaceAfter=8),
        "item_head": ParagraphStyle("ih", fontName=FONT_B, fontSize=11.5, leading=17.5,
                                     textColor=C_INK, spaceBefore=3, spaceAfter=4),
        "body": ParagraphStyle("b", fontName=FONT_R, fontSize=10.5, leading=16.5, textColor=C_INK),
        "answer": ParagraphStyle("a", fontName=FONT_R, fontSize=10.5, leading=17, textColor=C_INK),
    }


def _photo_flow(path: str, max_w=CONTENT_W, max_h=88 * mm):
    try:
        with PILImage.open(path) as im:
            iw, ih = im.size
    except Exception:
        return Paragraph(f'<font color="#E5484D">（照片文件缺失）</font>',
                         ParagraphStyle("err", fontName=FONT_R, fontSize=9, textColor=C_RED))
    s = min(max_w / iw, max_h / ih, 1.0)
    return Image(path, width=iw * s, height=ih * s)


def _trail(logs) -> str:
    if not logs:
        return ""
    marks = "".join(RESULT_MARK.get(r, "·") for r in logs[-8:])
    return f" · 轨迹 {marks}"


def _item_head(it: dict, idx: int, practice: bool, st: dict) -> Paragraph:
    kp = esc(it.get("kp_name") or "未分类")
    subj = esc(it.get("subject_label") or it.get("subject", ""))
    label = f"{idx}. 【{subj} · {kp}】"
    tail = ""
    if it.get("stubborn"):
        tail += ' <font color="#E5484D">［顽固］</font>'
    if not practice:
        reason = esc(it.get("reason_label") or "未归因")
        stage_txt = "已消灭" if it["status"] == "mastered" else f"阶段 {it['stage']}/5"
        tail += (f' <font size="9" color="#6B7280">错因：{reason} · 已错 {it["wrong_count"]} 次'
                 f' · {stage_txt}{_trail(it.get("logs"))}</font>')
    return Paragraph(label + tail, st["item_head"])


def _word_collection_line(it: dict, st: dict) -> Paragraph:
    prompt = esc(it.get("prompt") or "")
    wrong = esc(it.get("wrong_text") or "")
    correct = esc(it.get("correct_text") or "")
    return Paragraph(
        f"{prompt}　→　<font color='#E5484D'><strike>{wrong}</strike></font>"
        f"　→　<font color='#16A34A'><b>{correct}</b></font>", st["body"])


def _status_line(it: dict, st: dict) -> Paragraph | None:
    if it["status"] == "mastered":
        d = str(it.get("mastered_at") or "")[:10]
        return Paragraph(f'状态：<font color="#16A34A">已消灭（{esc(d)}）</font>', st["body"])
    if it["status"] == "paused":
        return Paragraph(f'状态：<font color="#B45309">已暂停</font>', st["body"])
    due = str(it.get("due_date") or "")
    return Paragraph(f"状态：复习中（下次复习 {esc(due)}）", st["body"])


def generate(template: str, items: list, photos: dict, logs: dict, meta: dict,
             variants: list | None = None) -> Path:
    """生成 PDF 并返回文件路径。

    template: collection | practice
    items:    序列化后的错题列表（dict，含 kp_name/subject_label/stubborn/...）
    photos:   {item_id: 原图绝对路径}
    logs:     {item_id: [result, ...]} 复习轨迹
    meta:     subject_label / term / stats / filters_desc / group_by / shuffle / stubborn_top
    variants: 重练卷可选「举一反三」变式题列表（question/answer/subject/subject_label/
              item_type/kp_name），印在答案页前，答案并入末页
    """
    register_fonts()
    ensure_dirs()
    practice = template == "practice"
    tpl_label = "重练卷" if practice else "收藏本"
    st = _styles()

    items = list(items)
    if meta.get("shuffle"):
        random.shuffle(items)
    if meta.get("stubborn_top"):
        items.sort(key=lambda x: (not x.get("stubborn"), -x.get("wrong_count", 0)))
    for it in items:
        it["logs"] = logs.get(it["id"], [])

    gen_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    gen_compact = datetime.now().strftime("%Y%m%d")
    name = f"错题本_{tpl_label}_{meta['subject_label']}_{meta['term']}_{gen_compact}.pdf"
    path = EXPORT_DIR / name

    doc = BaseDocTemplate(str(path), pagesize=A4, leftMargin=M, rightMargin=M,
                          topMargin=16 * mm, bottomMargin=22 * mm,
                          title=f"DoOver 错题本 · {tpl_label}", author=f"DoOver v{APP_VERSION}")

    def on_page(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(C_LINE)
        canvas.setLineWidth(0.5)
        canvas.line(M, 17 * mm, PAGE_W - M, 17 * mm)
        canvas.setFont(FONT_R, 8.5)
        canvas.setFillColor(C_GRAY)
        canvas.drawString(M, 11.5 * mm, f"DoOver 错题本 · {tpl_label}")
        canvas.drawCentredString(PAGE_W / 2, 11.5 * mm, f"第 {canvas.getPageNumber()} 页")
        canvas.drawRightString(PAGE_W - M, 11.5 * mm, f"{meta['term']} · {gen_date}")
        canvas.restoreState()

    frame = Frame(M, 22 * mm, CONTENT_W, PAGE_H - 38 * mm, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=on_page)])

    story: list = []

    # ---- 封面 ----
    title = f"{meta['subject_label']}错题{tpl_label}"
    story.append(Spacer(1, 26 * mm))
    story.append(Paragraph(title, st["cover_title"]))
    story.append(Paragraph(f"{meta['term']} · 共 {len(items)} 题 · 生成于 {gen_date}", st["cover_sub"]))
    story.append(Spacer(1, 12 * mm))
    s = meta.get("stats") or {}
    if practice:
        story.append(Paragraph("姓名：________________　　日期：________________　　用时：________",
                               st["cover_line"]))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"<font color='#E5484D'>［顽固］</font>标记的题建议家长陪同讲解，"
                               "答案在最后一页（可沿虚线撕下）", st["cover_line"]))
    else:
        story.append(Paragraph(
            f"收录 {s.get('total', 0)} 题 · 顽固 {s.get('stubborn', 0)} 题 · "
            f"已消灭 {s.get('mastered', 0)} 题 · 消灭率 {s.get('rate', 0)}%", st["cover_line"]))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(f"范围：{esc(meta.get('filters_desc') or '全部错题')}", st["cover_line"]))
    story.append(PageBreak())

    # ---- 分组 ----
    groups: list[tuple[str | None, list]] = []
    if meta.get("group_by") == "kp":
        seen: dict = {}
        for it in items:
            key = it.get("kp_name") or "未分类知识点"
            seen.setdefault(key, []).append(it)
        groups = list(seen.items())
    else:
        groups = [(None, items)]

    idx = 0
    answers: list[str] = []
    for gname, gitems in groups:
        if gname:
            story.append(Paragraph(esc(gname), st["group"]))
        for it in gitems:
            idx += 1
            block = [_item_head(it, idx, practice, st)]
            p = photos.get(it["id"])

            if it["item_type"] == "photo":
                if p:
                    block.append(_photo_flow(p))
                    block.append(Spacer(1, 2 * mm))
                ans = it.get("solution") or "见原题照片，家长核对"
                if practice:
                    block.append(AnswerLines(10))
                else:
                    if it.get("wrong_answer"):
                        block.append(Paragraph(
                            f'孩子答案：<font color="#B45309">{esc(it["wrong_answer"])}</font>',
                            st["body"]))
                    if it.get("solution"):
                        block.append(Paragraph(
                            f'正确做法：<font color="#0E6B57">{esc(it["solution"])}</font>',
                            st["body"]))
                    sl = _status_line(it, st)
                    if sl:
                        block.append(sl)
            else:  # word
                if practice:
                    block.append(Paragraph(f"默写：{esc(it.get('prompt') or '')}", st["body"]))
                    if it["subject"] == "chinese":
                        n = len(str(it.get("correct_text") or "").replace(" ", "")) or 1
                        block.append(Spacer(1, 1.5 * mm))
                        block.append(TianZiGe(n))
                    else:
                        w = len(str(it.get("correct_text") or ""))
                        block.append(Spacer(1, 1.5 * mm))
                        block.append(FourLineGrid(rows=1 if w <= 18 else 2))
                    ans = str(it.get("correct_text") or "")
                else:
                    block.append(_word_collection_line(it, st))
                    sl = _status_line(it, st)
                    if sl:
                        block.append(sl)
                    ans = str(it.get("correct_text") or "")

            if practice:
                subj = it.get("subject_label") or it.get("subject", "")
                answers.append(f"{idx}. 【{subj} · {esc(it.get('kp_name') or '')}】 {esc(ans)}")

            story.append(KeepTogether(block))
            story.append(Spacer(1, 5 * mm))

    # ---- 举一反三 · 变式题（仅重练卷，检验纠错效果） ----
    if practice and variants:
        story.append(KeepTogether([
            Paragraph("举一反三 · 变式题（检验纠错效果）", st["group"]),
            Paragraph("以下为卷内每道错题的同类新题；标注「挑战」的难度升一档，踿踿脚试试。做完再翻最后一页对答案。", st["body"]),
            Spacer(1, 2 * mm),
        ]))
        for i, v in enumerate(variants, 1):
            subj = esc(v.get("subject_label") or v.get("subject") or "")
            kp = esc(v.get("kp_name") or "未分类")
            tag = " · 挑战★" if v.get("hard") else ""
            block = [Paragraph(f"变{i}. 【{subj} · {kp}{tag}】", st["item_head"]),
                     Paragraph(esc(v.get("question") or ""), st["body"]),
                     Spacer(1, 1.5 * mm)]
            if v.get("item_type") == "word":
                if v.get("subject") == "chinese":
                    block.append(TianZiGe(4))
                else:
                    block.append(FourLineGrid(rows=1))
            else:
                block.append(AnswerLines(8))
            answers.append(f"变{i}. 【{kp}{' · 挑战' if v.get('hard') else ''}】 {esc(v.get('answer') or '')}")
            story.append(KeepTogether(block))
            story.append(Spacer(1, 5 * mm))

    # ---- 答案页（仅重练卷） ----
    if practice and answers:
        story.append(PageBreak())
        story.append(TearLine())
        story.append(Paragraph("参考答案", st["cover_title"]))
        story.append(Spacer(1, 4 * mm))
        for a in answers:
            story.append(Paragraph(a, st["answer"]))

    doc.build(story)

    from backup import prune_exports
    prune_exports()
    return path
