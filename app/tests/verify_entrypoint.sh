#!/bin/sh
# verify 容器入口：求解器/API 测试 -> 对 web 服务做 API 冒烟；任一失败则非零退出。
set -e

WEB_URL="${WEB_URL:-http://web:8080}"

echo "== [verify] stage 1/2: 求解器与 API 单元测试（镜像内执行）=="
cd /app 2>/dev/null || cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -p 'test_*.py' -v

echo "== [verify] stage 2/2: 本题 API 冒烟 -> ${WEB_URL} =="
python3 tests/smoke.py "${WEB_URL}"

echo "== [verify] 全部通过，verify 容器正常退出 (exit 0) =="
