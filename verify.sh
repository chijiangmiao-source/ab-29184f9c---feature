#!/usr/bin/env bash
# 一键构建并核验：等待 verify 容器结束并以其退出码作为本脚本退出码。
set -uo pipefail

cd "$(dirname "$0")"

HOST_PORT="${HOST_PORT:-8080}"
export HOST_PORT

echo ">> 构建镜像并启动 web + verify（宿主机访问端口 ${HOST_PORT}）..."
docker compose up --build \
  --abort-on-container-exit \
  --exit-code-from verify
code=$?

echo ">> verify 退出码: ${code}"
echo ">> web 仍在后台运行时可用: docker compose logs web / docker compose down"
exit ${code}
