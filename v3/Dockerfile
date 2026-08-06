# SO 数据分析 — Code Agent 沙箱
# 用法：docker build -t so-codeagent:latest .
#       docker run --rm -v $PWD/db:/sandbox/db:ro so-codeagent:latest claude -p "查 V4 服务商数量"
#
# 国内网络优化：apt 用阿里云、npm 用淘宝、pip 用清华，比官方源快 3-10 倍

FROM python:3.11-slim

# ─── apt 换阿里云源（国内服务器拉 deb.debian.org 慢）────
# python:3.11-slim 用 Debian Trixie (13)。Debian 12+ 用 .sources 格式
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi

# ─── 系统依赖 ────────────────────────────────
# - curl/ca-certs: 装 nodesource + 跑 LLM 调用
# - sqlite3: AI 沙箱里直接 sqlite3 db.sqlite "..." 查数
# - jq: agent 解 JSON 输出方便
# - fonts-noto-cjk: matplotlib 中文字体（不然图里中文是方框）
# - git: claude code 偶尔会用到
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        sqlite3 \
        jq \
        git \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

# ─── Node.js 20 + Claude Code CLI ────────────
# nodesource 走 CDN 国内还行；npm 强制换淘宝镜像（npmmirror.com）
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm config set registry https://registry.npmmirror.com \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# ─── Python 数据栈 ────────────────────────────
# pinned 大版本，让镜像缓存稳定。pip 用清华镜像
# python-docx：给周报/月报 SKILL 生成 .docx 报告用
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn \
    && pip install --no-cache-dir \
        pandas==2.2.* \
        numpy==1.26.* \
        matplotlib==3.8.* \
        openpyxl==3.1.* \
        scipy==1.13.* \
        pyarrow==15.* \
        tabulate==0.9.* \
        python-docx==1.1.*

# ─── 沙箱用户（非 root）────────────────────────
RUN useradd -m -u 1000 -s /bin/bash sandbox \
    && mkdir -p /sandbox/output /sandbox/work \
    && chown -R sandbox:sandbox /sandbox \
    && mkdir -p /home/sandbox/.claude \
    && chown -R sandbox:sandbox /home/sandbox

# ─── 沙箱内置 CLAUDE.md（系统 prompt）─────────
# 容器启动后 claude 会读 /sandbox/CLAUDE.md 作为项目级 system prompt
COPY --chown=sandbox:sandbox agent-claude.md /sandbox/CLAUDE.md

# ─── matplotlib 中文字体配置 ────────────────────
# 让 matplotlib 默认能渲染中文
ENV MPLBACKEND=Agg
RUN mkdir -p /home/sandbox/.config/matplotlib \
    && echo "font.family: Noto Sans CJK SC" > /home/sandbox/.config/matplotlib/matplotlibrc \
    && chown -R sandbox:sandbox /home/sandbox/.config

# ─── sandbox 用户也用淘宝 npm 源（万一 AI 装东西）─────
USER sandbox
RUN npm config set registry https://registry.npmmirror.com

WORKDIR /sandbox/work

# 默认进 bash；外部用 docker run ... claude -p "..." 覆盖
CMD ["bash"]
