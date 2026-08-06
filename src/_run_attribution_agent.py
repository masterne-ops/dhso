#!/usr/bin/env python3
"""产品归因 — 端到端 CLI:build attribution.json → 沙箱 agent 写归因 → 打印产出。

P2 验证用,也是 P3 page 后端逻辑的雏形(同一套:算数据→沙箱AI写归因→读回)。

  python3 _run_attribution_agent.py --start 2026-01 --end 2026-04 [--city 杭州市] [--top 3 --bottom 3]
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _loaders import DB_PATH  # noqa: E402
from _product_attribution import build  # noqa: E402
from _code_agent import run_agent, extract_text, is_configured  # noqa: E402

PROJECT_ROOT = Path(__file__).parent.parent
OUT_ROOT = PROJECT_ROOT / 'v3' / 'sandbox-output'

PROMPT = """执行 product-attribution SKILL:给产品视角经营归因写「归因」文字。
先读两份文档:
1. /sandbox/skills/system-overview/SKILL.md(全平台口径)
2. /sandbox/skills/product-attribution/SKILL.md(归因规范 + 你要写的键)
输入:/sandbox/output/work/attribution.json(已含 识别+下钻+9因素,无「归因」键)。
任务:按 SKILL 读数据 → 写顶层「总览归因」+ 每个亮点/问题切片的「归因」键 →
json.dump 写回原文件 /sandbox/output/work/attribution.json。
完成后 stdout 输出本期核心结论一句话。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--city', default=None)
    ap.add_argument('--top', type=int, default=3)
    ap.add_argument('--bottom', type=int, default=3)
    ap.add_argument('--min-wan', type=float, default=5.0)
    a = ap.parse_args()

    if not is_configured():
        print("❌ Anthropic 端点未配置(去 page 09 配)")
        sys.exit(1)

    sid = 'attr_' + uuid.uuid4().hex[:8]
    work = OUT_ROOT / sid / 'work'
    work.mkdir(parents=True, exist_ok=True)
    try:
        work.chmod(0o777)
    except OSError:
        pass

    print(f"📊 build attribution.json ({a.start}~{a.end}, city={a.city or '全省'}) …")
    rep = build(a.start, a.end, a.city, a.top, a.bottom, a.min_wan, with_factors=True)
    jp = work / 'attribution.json'
    jp.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(f"   ✅ {jp} ({jp.stat().st_size // 1024} KB) · "
          f"亮点{len(rep['亮点子系列'])}/问题{len(rep['问题子系列'])}")

    print(f"\n🤖 启动沙箱 agent 写归因(session {sid})…")
    for ev in run_agent(PROMPT, db_path=DB_PATH, output_root=OUT_ROOT,
                        ui_session_id=sid, max_turns=30, log_to_db=False):
        t = ev.get('type')
        if t == 'assistant':
            txt = extract_text(ev)
            if txt:
                print("  🗣", txt.strip()[:400])
        elif t == 'result':
            print(f"  [result] cost={ev.get('cost')} dur={ev.get('duration')}")
        elif t == 'done':
            print(f"  [done] files={ev.get('files')}")
        elif t == 'error':
            print(f"  ❌ {ev.get('error')}")

    print("\n========== 归因产出 ==========")
    jj = json.loads(jp.read_text(encoding='utf-8'))
    ov = jj.get('总览归因')
    print("\n【总览归因】")
    print(json.dumps(ov, ensure_ascii=False, indent=2) if ov else "  (空!)")
    for tag in ('亮点子系列', '问题子系列'):
        for s in jj.get(tag, []):
            g = s.get('归因')
            print(f"\n【{tag}】{s['子系列']} (增量{s['增量_万']}万 / 同比{s['同比']})")
            print(json.dumps(g, ensure_ascii=False, indent=2) if g else "  (空!)")


if __name__ == '__main__':
    main()
