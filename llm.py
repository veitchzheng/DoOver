"""大模型错题分析 · 多供应商可切换（OpenAI 兼容协议）

配置文件 data/llm.json（已被 .gitignore 排除，API key 不入库）：
{
  "active": "deepseek",       ← 文本分析模型（考点/易错点/提示/归因建议）
  "vision_active": "qwen-vl", ← 看图模型（照片题 OCR 题干，可选；不填或未配置则跳过 OCR）
  "providers": {
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "api_key": "sk-...", "model": "deepseek-chat"},
    "qwen-vl":  {"label": "通义千问 VL", "base_url": "...", "api_key": "...",
                 "model": "qwen-vl-max", "vision": true},
    ...
  }
}

任何 OpenAI 兼容端点（DeepSeek / 通义千问 / GLM / Kimi / Ollama 本地等）
都可以直接加进 providers；api_key 为空视为未配置，对应功能自动关闭。
"""
import base64
import json
import re
import urllib.error
import urllib.request

from config import DATA_DIR, REASONS, SUBJECTS

LLM_FILE = DATA_DIR / "llm.json"
TIMEOUT = 60          # 单次请求超时（秒）
POINT_MAX = 50        # 考点落库长度上限
PITFALL_MAX = 120     # 易错点落库长度上限
HINT_MAX = 80         # 单条提示长度上限
HINT_COUNT = 2        # 渐进提示档数
KP_MAX = 20           # 建议知识点名称长度上限
QUESTION_MAX = 500    # OCR 题干落库长度上限

SYSTEM_PROMPT = """你是资深小学老师，负责分析小学生错题。根据提供的错题信息，输出这道题的【考点】【易错点】和复习辅助信息。
要求：
1. points 考点：一两个短语点明考查的知识点或能力，用顿号分隔，不超过 30 字
2. pitfalls 易错点：一到两句话，结合"孩子的错误答案"和"正确做法"具体指出错在哪、怎么避免，不超过 70 字
3. hints 复习提示：给孩子的两档渐进提示（用于翻面对答案前的思考引导），数组恰好 2 个字符串：
   - 第一档：只给思考方向，例如"先想想这题要先算什么"，绝不透露答案或关键数字
   - 第二档：给关键步骤的思路点拨，仍然不给出最终答案
   - 语气亲切，面向小学三四年级孩子
4. reason 归因建议：从 concept（概念不会）/ careless（审题马虎）/ calc（计算失误）/ clueless（完全没思路）中选一个最可能的；信息不足以判断时填 null
5. kp 知识点建议：一个最贴切的知识点短名称（不超过 12 字，如"混合运算"、"形近字"）；无法判断时填 null
6. 语言面向小学生家长，简明具体，不说空话套话；信息不足时基于现有信息给出最可能的分析
7. 只输出 JSON，格式：{"points": "…", "pitfalls": "…", "hints": ["…", "…"], "reason": "…", "kp": "…"}，不要输出其他任何内容"""

HINT_SYSTEM_PROMPT = """你是亲切的小学辅导老师。根据错题信息，给孩子生成两档渐进式思考提示。
要求：
1. 第一档提示：只给思考方向（如"先想想这题要先算什么"），语气鼓励，绝不透露答案或关键数字
2. 第二档提示：给关键步骤的思路点拨，仍然不给出最终答案
3. 每条不超过 60 字，面向小学三四年级孩子，用"你"称呼
4. 只输出 JSON：{"hints": ["第一档", "第二档"]}，不要输出其他内容"""

VARIANT_SYSTEM_PROMPT = """你是出题老师。根据一道小学生错题，为它出一道【变式题】：考查同一知识点、同难度水平（小学三四年级），换情境换数字但不换考点。
要求：
1. question：题干完整自包含，孩子不看原题也能独立作答；不超过 150 字；语文/英语错字词类出"看提示写一写"式的新字词题（换一个同样易混淆的字词）
2. answer：给出正确答案，外加一句话解析；不超过 120 字
3. 难度与原题相当，不要更难；数字算式要能口算/竖式解决
4. 只输出 JSON：{"question": "…", "answer": "…"}，不要输出其他内容"""

OCR_SYSTEM_PROMPT = """你是 OCR 助手。请从这张小学错题照片中逐字提取题目原文（含算式），
按阅读顺序整理成连续文本，保留原有标点与换行；忽略书写潦草的答案痕迹与照片水印。
只输出题目原文文字，不要解释、不要添加任何内容。若照片中没有题目文字，输出空字符串。"""


def _read_llm_json():
    """读 llm.json 并解析。对手改最常见的笔误——最后一个属性后多写逗号
    （如删行后残留的悬空逗号）——做兼容清理后重试，避免家长小笔误静默关闭全部 AI 功能。"""
    text = LLM_FILE.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


def load_config(which="active"):
    """读取并校验 llm.json 中指定的模型配置；任何异常都返回 None（功能关闭）。
    which: "active"=文本分析模型，"vision_active"=看图模型。"""
    try:
        raw = _read_llm_json()
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return None
    pid = raw.get(which)
    p = providers.get(pid) if isinstance(pid, str) else None
    if not isinstance(p, dict):
        return None
    base_url = str(p.get("base_url") or "").strip().rstrip("/")
    api_key = str(p.get("api_key") or "").strip()
    model = str(p.get("model") or "").strip()
    if not base_url or not model or not api_key:
        return None
    return {
        "id": pid,
        "label": str(p.get("label") or pid),
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "vision": bool(p.get("vision")),
        "proxy": str(p.get("proxy") or "").strip() or None,
        "max_tokens": int(p.get("max_tokens") or 600),
        "temperature": float(p.get("temperature") if p.get("temperature") is not None else 0.2),
        "extra_body": p.get("extra_body") if isinstance(p.get("extra_body"), dict) else None,
    }


def is_configured(which="active") -> bool:
    return load_config(which) is not None


def status(which="active") -> dict:
    """供前端/接口展示的状态（绝不包含 api_key）"""
    cfg = load_config(which)
    if not cfg:
        return {"configured": False, "label": None, "model": None}
    return {"configured": True, "label": cfg["label"], "model": cfg["model"]}


def _opener(cfg: dict):
    """构建请求 opener：默认明确直连（不受系统/环境代理影响，避免代理启停导致服务中断）；
    供应商配置了 proxy 时才走代理。urllib 默认 opener 会缓存首次调用时的代理设置，
    长驻服务在用户代理状态变化时会持续失败，故这里每次显式构建。"""
    proxy = (cfg or {}).get("proxy")
    if proxy:
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        handler = urllib.request.ProxyHandler({})   # {} = 禁用所有代理，强制直连
    return urllib.request.build_opener(handler)


def chat(messages, cfg=None) -> str:
    """调用 OpenAI 兼容 /chat/completions，返回首条回复文本。
    messages 支持纯文本与 OpenAI 视觉格式（content 为数组的消息）。"""
    cfg = cfg or load_config()
    if not cfg:
        raise RuntimeError("大模型未配置（请编辑 data/llm.json）")
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"],
        "max_tokens": cfg["max_tokens"],
    }
    if cfg.get("extra_body"):  # 供应商特有参数透传（如硅基流动 Qwen3 的 enable_thinking）
        body.update(cfg["extra_body"])
    body = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        cfg["base_url"] + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["api_key"]})
    try:
        with _opener(cfg).open(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise RuntimeError(f"大模型接口返回 {e.code}：{detail or e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接大模型服务（已尝试直连）：{e.reason}") from e
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"大模型响应格式异常：{str(data)[:200]}")
    if not str(content).strip():
        finish = ""
        try:
            finish = data["choices"][0].get("finish_reason") or ""
        except Exception:
            pass
        if finish == "length":
            raise RuntimeError("模型思考用尽了 token 上限（推理型模型请在 llm.json 调大该供应商的 max_tokens）")
        raise RuntimeError("大模型返回了空内容")
    return str(content).strip()


def build_prompt(item: dict, term: str = "") -> list:
    """把错题字段拼成分析提示词；照片题优先使用 OCR 出的题干原文"""
    lines = [f"科目：{SUBJECTS.get(item.get('subject'), item.get('subject') or '未知')}"]
    if term:
        lines.append(f"学期：{term}")
    if item.get("question_text"):
        lines.append(f"题目原文：{item['question_text']}")
    if item.get("kp_name"):
        lines.append(f"家长标注的知识点：{item['kp_name']}")
    if item.get("reason_label"):
        lines.append(f"家长标注的错因：{item['reason_label']}")
    if item.get("item_type") == "word":
        lines.append("题型：看提示写字词")
        lines.append(f"提示：{item.get('prompt') or ''}")
        lines.append(f"孩子错写：{item.get('wrong_text') or ''}")
        lines.append(f"正确写法：{item.get('correct_text') or ''}")
    else:
        lines.append("题型：拍照题（题目原文在照片中，此处无法提供，"
                     "请结合下方错误答案与正确做法推断考点）"
                     if not item.get("question_text") else "题型：拍照题（题干见上方题目原文）")
        if item.get("wrong_answer"):
            lines.append(f"孩子的错误答案/错误做法：{item['wrong_answer']}")
        if item.get("solution"):
            lines.append(f"正确做法：{item['solution']}")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)}]


def _clip_str(v, n):
    return str(v or "").strip()[:n]


def parse_analysis(text: str) -> dict:
    """从模型回复中提取分析结果，容忍 markdown 代码块与字段缺失。
    返回 {"points","pitfalls","hints","reason","kp"}。"""
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError("模型回复不是合法 JSON")
    try:
        payload = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise RuntimeError("模型回复 JSON 解析失败")
    if not isinstance(payload, dict):
        raise RuntimeError("模型回复结构不正确")
    points = _clip_str(payload.get("points"), POINT_MAX)
    pitfalls = _clip_str(payload.get("pitfalls"), PITFALL_MAX)
    if not points and not pitfalls:
        raise RuntimeError("模型未给出有效的考点或易错点")
    hints = payload.get("hints")
    if not isinstance(hints, list):
        hints = []
    hints = [_clip_str(h, HINT_MAX) for h in hints if _clip_str(h, HINT_MAX)][:HINT_COUNT]
    reason = payload.get("reason")
    reason = reason if reason in REASONS else None
    kp = _clip_str(payload.get("kp"), KP_MAX) or None
    return {"points": points, "pitfalls": pitfalls, "hints": hints,
            "reason": reason, "kp": kp}


def analyze_item(item: dict, term: str = "") -> dict:
    """分析一道错题：组提示词 → 调模型 → 解析结果；返回含所用模型名"""
    cfg = load_config()
    if not cfg:
        raise RuntimeError("大模型未配置（请编辑 data/llm.json）")
    res = parse_analysis(chat(build_prompt(item, term), cfg))
    res["model"] = cfg["model"]
    return res


def generate_hints(item: dict, term: str = "") -> list:
    """为已入库但缺提示的错题懒生成两档思考提示（提示按钮首次点击时）"""
    cfg = load_config()
    if not cfg:
        raise RuntimeError("大模型未配置（请编辑 data/llm.json）")
    item = dict(item)
    lines = [f"科目：{SUBJECTS.get(item.get('subject'), item.get('subject') or '未知')}"]
    if term:
        lines.append(f"学期：{term}")
    if item.get("item_type") == "word":
        lines.append(f"提示项：{item.get('prompt') or ''}")
        lines.append(f"孩子错写：{item.get('wrong_text') or ''}")
        lines.append("注意：不要直接说出正确写法，从字形/偏旁/词义方向引导")
    else:
        if item.get("question_text"):
            lines.append(f"题目原文：{item['question_text']}")
        if item.get("wrong_answer"):
            lines.append(f"孩子的错误答案：{item['wrong_answer']}")
        if item.get("solution"):
            lines.append(f"正确做法：{item['solution']}")
        lines.append("注意：不要直接说出答案，只引导思路")
    messages = [{"role": "system", "content": HINT_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)}]
    text = chat(messages, cfg)
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError("提示生成回复不是合法 JSON")
    try:
        hints = json.loads(m.group(0)).get("hints")
    except json.JSONDecodeError:
        raise RuntimeError("提示生成回复解析失败")
    if not isinstance(hints, list):
        raise RuntimeError("提示生成回复结构不正确")
    hints = [_clip_str(h, HINT_MAX) for h in hints if _clip_str(h, HINT_MAX)][:HINT_COUNT]
    if not hints:
        raise RuntimeError("模型没有给出可用提示")
    return hints


def parse_variant(text: str) -> dict:
    """从变式题生成回复中提取 {"question","answer"}，容忍 markdown 围栏"""
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError("模型回复不是合法 JSON")
    try:
        payload = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise RuntimeError("模型回复 JSON 解析失败")
    if not isinstance(payload, dict):
        raise RuntimeError("模型回复结构不正确")
    question = _clip_str(payload.get("question"), 200)
    answer = _clip_str(payload.get("answer"), 200)
    if not question:
        raise RuntimeError("模型未给出有效题干")
    return {"question": question, "answer": answer or "（答案不完整，建议家长重新生成）"}


def generate_variant(item: dict, kp_name: str, term: str = "") -> dict:
    """为已消灭的错题生成一道同知识点变式题（消灭彩蛋用）"""
    cfg = load_config()
    if not cfg:
        raise RuntimeError("大模型未配置（请编辑 data/llm.json）")
    lines = [f"科目：{SUBJECTS.get(item.get('subject'), item.get('subject') or '未知')}"]
    if term:
        lines.append(f"学期：{term}")
    lines.append(f"知识点：{kp_name or '未分类'}")
    if item.get("item_type") == "word":
        lines.append("原题（看提示写字词）：")
        lines.append(f"提示：{item.get('prompt') or ''}")
        lines.append(f"正确写法：{item.get('correct_text') or ''}（孩子当时错写成：{item.get('wrong_text') or ''}）")
    else:
        lines.append("原题：")
        if item.get("question_text"):
            lines.append(f"题目原文：{item['question_text']}")
        else:
            lines.append("（题目原文在照片中无法提供，请依据下方信息把握考点）")
        if item.get("wrong_answer"):
            lines.append(f"孩子当时错在：{item['wrong_answer']}")
        if item.get("solution"):
            lines.append(f"正确做法：{item['solution']}")
    messages = [{"role": "system", "content": VARIANT_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(lines)}]
    res = parse_variant(chat(messages, cfg))
    res["model"] = cfg["model"]
    return res


def extract_question(image_b64: str, cfg: dict = None) -> str:
    """视觉模型 OCR：从题目照片提取题干文字，返回整理后的文本"""
    cfg = cfg or load_config("vision_active")
    if not cfg:
        raise RuntimeError("看图模型未配置（请编辑 data/llm.json 的 vision_active）")
    if not cfg.get("vision"):
        raise RuntimeError("当前看图模型未标记 vision 能力")
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": OCR_SYSTEM_PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
        ]},
    ]
    text = chat(messages, cfg).strip()
    if len(text) > QUESTION_MAX:
        text = text[:QUESTION_MAX]
    return text
