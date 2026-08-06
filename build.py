#!/usr/bin/env python3
"""
SO 数据分析平台 - 打包脚本
生成可分发的 zip：包含代码、脚本、README，不含 venv/db/data/reports 等。
依赖：仅 Python 标准库。

用法:
    python3 build.py             # 默认版本号 v1.0
    python3 build.py --version 1.1
"""

import argparse
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DIST_DIR = ROOT / "dist"
APP_NAME = "SO数据分析平台"

# 目录黑名单（含其下所有内容都跳过）
EXCLUDE_DIR_PARTS = {
    "venv", "db", "data", "reports",
    "__pycache__", "dist", ".git", ".idea", ".vscode",
    "sandbox-output",
}
# 文件扩展名黑名单
EXCLUDE_EXT = {".pyc", ".pyo", ".db", ".xlsx", ".xls", ".log"}
# 完整文件名黑名单
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}
# 显式只允许的根级文件 / 目录（白名单更安全）
INCLUDE_ROOT = {
    "README.md",
    "requirements.txt",
    "install.sh", "install.bat",
    "start.sh", "start.bat",
    "deploy.sh",              # 本地一键部署/更新到生产服务器（rsync + ssh）
    "src",
    "ops",                    # 服务器运维（systemd / logrotate / cron）
}


def should_exclude(rel: Path) -> bool:
    parts = rel.parts
    if any(p in EXCLUDE_DIR_PARTS for p in parts):
        return True
    if rel.name in EXCLUDE_NAMES:
        return True
    if rel.suffix.lower() in EXCLUDE_EXT:
        return True
    # 只接受白名单根
    if parts and parts[0] not in INCLUDE_ROOT:
        return True
    return False


def collect_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if should_exclude(rel):
            continue
        files.append(path)
    return files


def build_zip(version: str) -> Path:
    DIST_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out = DIST_DIR / f"{APP_NAME}-v{version}-{stamp}.zip"
    if out.exists():
        out.unlink()

    files = collect_files()
    print(f"打包根目录: {ROOT}")
    print(f"输出位置  : {out}")
    print(f"包含文件  : {len(files)} 个")
    print()
    print("文件清单:")
    for f in files:
        rel = f.relative_to(ROOT)
        size = f.stat().st_size
        print(f"  + {rel}  ({size:,} B)")

    # zipfile 默认 utf-8 文件名，对中文文件名兼容性最好
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in files:
            rel = f.relative_to(ROOT)
            # 解压后顶层目录用 APP_NAME，避免污染当前目录
            arcname = Path(APP_NAME) / rel
            zf.write(f, arcname=str(arcname))

    size_mb = out.stat().st_size / 1024 / 1024
    print()
    print(f"✅ 打包完成 — {size_mb:.1f} MB")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.0", help="版本号（默认 1.0）")
    args = parser.parse_args()

    out = build_zip(args.version)

    print()
    print("=" * 50)
    print("分发说明（README 内已包含详细安装步骤）：")
    print("=" * 50)
    print(f"1. 把 {out.name} 发给同事")
    print("2. 同事解压后进入文件夹：")
    print("   - macOS / Linux: ./install.sh && ./start.sh")
    print("   - Windows      : 双击 install.bat → 双击 start.bat")
    print("3. 浏览器访问 http://localhost:8501")
    print("4. 主页『📥 数据导入』→ 选择数据类型 → 上传 Excel")
    print()


if __name__ == "__main__":
    main()
