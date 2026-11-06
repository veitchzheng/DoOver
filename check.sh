#!/bin/bash
# DoOver 错题本 · 自检脚本
# 用法：
#   ./check.sh                            # 在线自检（服务健康/库完整性/磁盘/备份年龄）
#   ./check.sh --host 192.168.1.10:8200   # 指定服务地址
#   ./check.sh --verify-backup <zip路径>  # 校验某份备份是否可完整恢复
set -o pipefail

cd "$(dirname "$0")"
PY=".venv/bin/python"; [ -x "$PY" ] || PY="python3"
HOST="127.0.0.1:8200"
VERIFY_ZIP=""

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --verify-backup) VERIFY_ZIP="$2"; shift 2 ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac
done

if [ -n "$VERIFY_ZIP" ]; then
  echo "== 校验备份: $VERIFY_ZIP =="
  exec "$PY" backup.py --verify "$VERIFY_ZIP"
fi

echo "== DoOver 自检 → http://$HOST =="
HEALTH=$(curl -s -m 8 "http://$HOST/api/health" || true)
if [ -z "$HEALTH" ]; then
  echo "✗ 服务无响应（未启动？端口/地址不对？）"; exit 1
fi

RESULT_CODE=0
"$PY" - "$HEALTH" <<'PYEOF' || RESULT_CODE=$?
import json
import sys
h = json.loads(sys.argv[1])
problems, warns = [], []
if h.get("status") != "ok":
    problems.append(f"服务状态异常: {h.get('status')}")
if h.get("db") != "ok":
    problems.append(f"数据库完整性检查未通过: {h.get('db')}")
disk = h.get("disk_free_mb", 0)
if disk < 1024:
    warns.append(f"磁盘剩余空间不足 1GB（{disk}MB）")
b = h.get("backup") or {}
latest = b.get("latest")
if not latest:
    warns.append("还没有任何备份")
elif latest.get("age_hours", 999) > 48:
    warns.append(f"最近备份已 {latest['age_hours']} 小时（超过 48h 阈值）")

print(f"服务状态   : {h.get('status')}  版本 {h.get('version')}")
print(f"数据库     : quick_check={h.get('db')}  错题 {h.get('items')} 道")
print(f"磁盘剩余   : {disk} MB")
if latest:
    print(f"最近备份   : {latest['name']}（{latest['size']} 字节，{latest['age_hours']} 小时前，共 {b.get('count')} 份）")
else:
    print("最近备份   : 无")
for w in warns:
    print(f"⚠ 警告: {w}")
for p in problems:
    print(f"✗ 异常: {p}")
sys.exit(1 if problems else 0)
PYEOF
[ $RESULT_CODE -eq 0 ] && echo "== 自检通过 ==" || echo "== 自检未通过 =="
exit $RESULT_CODE
