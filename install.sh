#!/bin/bash
# SO 数据分析平台 - 一键安装部署脚本
# 适用：macOS / Linux
# 自动检测并安装 Python，所有下载使用国内镜像源（清华）
# 含两个分析模块：📈 产品流向分析、📊 SO 环比分析

echo "=========================================="
echo "  SO 数据分析平台 - 一键安装脚本"
echo "=========================================="
echo ""
echo "提示：此脚本将自动安装 Python（如未安装）"
echo "      所有下载均使用国内镜像源（清华）"
echo ""

cd "$(dirname "$0")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
PIP_HOST="pypi.tuna.tsinghua.edu.cn"

# [1/10] 检查项目文件
echo "[1/10] 检查项目文件..."
if [ ! -d "src" ] || [ ! -f "src/app.py" ]; then
    echo -e "${RED}[错误] src/app.py 不存在，请先解压完整目录${NC}"
    exit 1
fi
echo -e "${GREEN}[OK] 项目文件检查通过${NC}"

# [2/10] Python
echo ""
echo "[2/10] 检查 Python 环境..."
if command -v python3 &> /dev/null; then
    echo -e "${GREEN}[OK] $(python3 --version)${NC}"
else
    echo -e "${YELLOW}[信息] 未找到 python3，开始安装...${NC}"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if ! command -v brew &> /dev/null; then
            /bin/bash -c "$(curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/install.git/install.sh)" 2>/dev/null \
                || { echo -e "${RED}[错误] Homebrew 安装失败${NC}"; exit 1; }
        fi
        brew install python3 2>/dev/null || { echo -e "${RED}[错误] Python 安装失败${NC}"; exit 1; }
    elif command -v apt-get &> /dev/null; then
        apt-get update -qq 2>/dev/null
        apt-get install -y python3 python3-pip python3-venv 2>/dev/null || { echo -e "${RED}[错误] apt-get 失败${NC}"; exit 1; }
    elif command -v yum &> /dev/null; then
        yum install -y python3 python3-pip 2>/dev/null || { echo -e "${RED}[错误] yum 失败${NC}"; exit 1; }
    else
        echo -e "${RED}[错误] 请手动安装 Python 3.10+${NC}"; exit 1
    fi
    echo -e "${GREEN}[OK] Python 安装成功${NC}"
fi

# [3/10] pip
echo ""
echo "[3/10] 检查 pip..."
python3 -m pip --version &> /dev/null || python3 -m ensurepip --default-pip 2>/dev/null
python3 -m pip --version &> /dev/null || { echo -e "${RED}[错误] pip 不可用${NC}"; exit 1; }
echo -e "${GREEN}[OK] pip 就绪${NC}"

# [4/10] venv
echo ""
echo "[4/10] 创建虚拟环境..."
[ -d "venv" ] && rm -rf venv
python3 -m venv venv || { echo -e "${RED}[错误] venv 创建失败${NC}"; exit 1; }
source venv/bin/activate
echo -e "${GREEN}[OK] 虚拟环境已激活${NC}"

# [5/10] pip 镜像
echo ""
echo "[5/10] 配置 pip 国内镜像（清华）..."
mkdir -p ~/.pip 2>/dev/null
cat > ~/.pip/pip.conf << EOF
[global]
index-url = $PIP_INDEX
trusted-host = $PIP_HOST
EOF
echo -e "${GREEN}[OK] 镜像配置完成${NC}"

# [6/10] 升级 pip
echo ""
echo "[6/10] 升级 pip..."
pip install --upgrade pip -q --index-url "$PIP_INDEX"
echo -e "${GREEN}[OK] pip 升级完成${NC}"

# [7/10] 依赖
echo ""
echo "[7/10] 安装依赖（清华镜像）..."
echo "  - streamlit (Web 框架)"
echo "  - pandas / numpy (数据)"
echo "  - openpyxl (Excel I/O)"
echo "  - matplotlib (产品流向图表导出)"
echo ""
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --index-url "$PIP_INDEX" || { echo -e "${RED}[错误] 依赖安装失败${NC}"; exit 1; }
else
    pip install streamlit pandas numpy openpyxl matplotlib --index-url "$PIP_INDEX" || { echo -e "${RED}[错误] 依赖安装失败${NC}"; exit 1; }
fi
echo -e "${GREEN}[OK] 依赖安装完成${NC}"

# [8/10] 目录
echo ""
echo "[8/10] 创建必要目录..."
mkdir -p data db reports 2>/dev/null
echo -e "${GREEN}[OK] data / db / reports${NC}"

# [9/10] Streamlit 配置
echo ""
echo "[9/10] 配置 Streamlit..."
mkdir -p ~/.streamlit 2>/dev/null
cat > ~/.streamlit/config.toml << EOF
[server]
headless = true

[browser]
gatherUsageStats = false
EOF
echo -e "${GREEN}[OK] Streamlit 配置完成${NC}"

# [10/10] start.sh
echo ""
echo "[10/10] 启动脚本就绪（start.sh 已存在）"
chmod +x start.sh 2>/dev/null

echo ""
echo "=========================================="
echo -e "  ${GREEN}✅ 安装完成${NC}"
echo "=========================================="
echo ""
echo "【启动应用】"
echo "  ./start.sh                # 默认端口 8501"
echo "  ./start.sh 8503           # 自定义端口"
echo ""
echo "【浏览器访问】"
echo "  http://localhost:8501"
echo ""
echo "【使用流程】"
echo "  1. 主页『📥 数据导入』上传 Excel"
echo "  2. 左侧导航选择分析模块："
echo "     - 📈 产品流向分析（同比 2025 vs 2026）"
echo "     - 📊 SO 环比分析（任意两月对比）"
echo "  3. 命令行批量导出：python src/export_city_report.py"
echo ""
echo "【卸载】"
echo "  rm -rf venv"
echo ""
