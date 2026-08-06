#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 master JSON 内联进 HTML,生成「分城市独立单文件」+「全省版」。
单文件 = 任意浏览器双击即用,不依赖服务器/外部 json。本地运行,无需 DB:
    python3 build_dist.py
"""
import json, os

HERE     = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, '地市月度SO作战方案.html')
MASTER   = os.path.join(HERE, 'so_plan_data.json')
OUTDIR   = os.path.join(HERE, '分发')
os.makedirs(OUTDIR, exist_ok=True)

html = open(TEMPLATE, encoding='utf-8').read()
data = json.load(open(MASTER, encoding='utf-8'))
idx  = html.index('<script>')           # 主脚本前注入内联数据(模板仅一个 script 块)

def build(subset, title):
    payload = json.dumps(subset, ensure_ascii=False).replace('</', '<\\/')   # 防 </script> 截断
    out = html[:idx] + f'<script>window.EMBEDDED_DATA={payload};</script>\n' + html[idx:]
    return out.replace('<title>地市月度SO作战方案 · 2026年6月</title>',
                       f'<title>{title} · 月度SO作战方案 · 2026年6月</title>')

meta_base = {k: v for k, v in data['meta'].items() if k != '省合计'}   # 分城市不带省汇总→单城市模式
n = 0
for city, cd in data['cities'].items():
    fn = os.path.join(OUTDIR, f'{city}_SO作战方案.html')
    open(fn, 'w', encoding='utf-8').write(build({'meta': meta_base, 'cities': {city: cd}}, city))
    print(f'  ✓ {city}_SO作战方案.html ({os.path.getsize(fn)//1024} KB)'); n += 1

fn = os.path.join(OUTDIR, '浙江省_SO作战方案_全省版.html')
open(fn, 'w', encoding='utf-8').write(build(data, '浙江省'))
print(f'  ✓ 浙江省_SO作战方案_全省版.html ({os.path.getsize(fn)//1024} KB)')
print(f'\n完成 {n} 个分城市 + 1 全省版 → {OUTDIR}/')
