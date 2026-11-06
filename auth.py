"""鉴权：HMAC 签名 Cookie（HttpOnly）+ 访问码哈希 + 登录防爆破"""
import hashlib
import hmac
import time

from config import SESSION_DAYS
from db import load_secret

COOKIE_NAME = "doover_session"
_SECRET = None  # 惰性加载


def _secret() -> bytes:
    global _SECRET
    if _SECRET is None:
        _SECRET = load_secret().encode()
    return _SECRET


def make_token(role: str = "parent", exp_days: int = SESSION_DAYS) -> str:
    exp = int(time.time()) + exp_days * 86400
    payload = f"{exp}.{role}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def check_token(token: str):
    """验证会话；有效返回身份（'parent'/'child'），无效返回 None"""
    try:
        exp_str, role, sig = token.split(".")
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return None
    if role not in ("parent", "child") or exp < time.time():
        return None
    expect = hmac.new(_secret(), f"{exp_str}.{role}".encode(), hashlib.sha256).hexdigest()
    return role if hmac.compare_digest(sig, expect) else None


def hash_code(code: str) -> str:
    return hashlib.sha256(_secret() + code.encode()).hexdigest()


# ---- 登录防爆破：同 IP 连续失败 10 次锁定 10 分钟（内存级，家用足够） ----
_FAILS: dict[str, tuple[int, float]] = {}
MAX_FAILS = 10
LOCK_SECONDS = 600


def _now():
    return time.time()


def is_locked(ip: str) -> bool:
    rec = _FAILS.get(ip)
    if not rec:
        return False
    fails, until = rec
    if until and until > _now():
        return True
    if until and until <= _now():
        _FAILS.pop(ip, None)
    return False


def record_fail(ip: str):
    fails, _ = _FAILS.get(ip, (0, 0))
    fails += 1
    until = _now() + LOCK_SECONDS if fails >= MAX_FAILS else 0
    _FAILS[ip] = (fails, until)


def clear_fails(ip: str):
    _FAILS.pop(ip, None)
