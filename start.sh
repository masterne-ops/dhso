#!/bin/bash
# SO 数据分析平台 - 启动脚本（macOS / Linux）

cd "$(dirname "$0")"

echo "=========================================="
echo "  SO 数据分析平台 - 启动中..."
echo "=========================================="
echo ""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

export no_proxy="localhost,127.0.0.1,::1"
export http_proxy=""
export https_proxy=""

if [ ! -f "venv/bin/activate" ]; then
    echo -e "${RED}[错误] 找不到虚拟环境（venv 不存在）${NC}"
    echo -e "${YELLOW}请先运行：./install.sh${NC}"
    exit 1
fi
source venv/bin/activate

mkdir -p ~/.streamlit
cat > ~/.streamlit/config.toml << 'STREAMLIT_EOF'
[server]
headless = true

[browser]
gatherUsageStats = false
STREAMLIT_EOF

if [ ! -f "src/app.py" ]; then
    echo -e "${RED}[错误] 找不到 src/app.py${NC}"
    exit 1
fi

PORT=${1:-8501}

echo "=========================================="
echo "  浏览器访问：http://localhost:$PORT"
echo "  Ctrl+C 停止"
echo "=========================================="
echo ""

python3 -m streamlit run src/app.py --server.port "$PORT" --server.headless true

echo ""
echo "=========================================="
echo "  Streamlit 已停止"
echo "=========================================="
