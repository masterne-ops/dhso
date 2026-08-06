#!/bin/bash
# SO 数据分析平台 - 打包入口（macOS / Linux）
# 用法: ./build.sh [版本号]    例: ./build.sh 1.1

cd "$(dirname "$0")"

VERSION=${1:-1.0}

echo "=========================================="
echo "  SO 数据分析平台 - 打包"
echo "  版本: $VERSION"
echo "=========================================="
echo ""

if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 python3"
    exit 1
fi

python3 build.py --version "$VERSION"
