#!/usr/bin/env bash
# 构建 Code Agent 沙箱镜像
set -euo pipefail

cd "$(dirname "$0")"

IMAGE_TAG="${IMAGE_TAG:-so-codeagent:latest}"

echo "🔨 构建沙箱镜像: $IMAGE_TAG"
echo "   预计 5 分钟（首次拉 base + 装 Node + 装 Python 库）"
echo

if ! command -v docker >/dev/null 2>&1; then
    echo "❌ 未检测到 docker 命令"
    echo "   Mac/Win:   https://www.docker.com/products/docker-desktop/"
    echo "   Linux:     curl -fsSL https://get.docker.com | sh"
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "❌ docker daemon 未启动"
    echo "   Mac:  打开 Docker Desktop"
    echo "   Linux: sudo systemctl start docker"
    exit 1
fi

docker build -t "$IMAGE_TAG" .

echo
echo "✅ 构建完成: $IMAGE_TAG"
echo
echo "🧪 冒烟测试（不连 LLM，只验证 CLI 能起来）:"
echo "   docker run --rm $IMAGE_TAG claude --version"
echo
echo "🚀 正式跑（在 streamlit 页面 09 配好端点后自动调）"
