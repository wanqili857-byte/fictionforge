#!/usr/bin/env python3
"""
run_chapter.py — 顶层协调器 CLI（v0.2.0 认知反转引擎）。

每章经 ChapterCoordinator 分发到 gen / engine / hybrid 三种管线路径：
  - gen：手写 spec → 理论心智层标注（info_gaps）→ 推进知识 → 生成正文
  - engine：引擎 tick → JSON 落盘，不生成正文
  - hybrid：引擎 tick → 机械 spec → 理论心智层标注 → 生成正文

用法:
    python3 scripts/run_chapter.py novels/静默轨道 --chapter 5
    python3 scripts/run_chapter.py novels/静默轨道 --chapter 5 --mode hybrid --arc arcs/arc1.md
    python3 scripts/run_chapter.py novels/静默轨道 --chapter 5 --mode gen --spec specs/ch5.json --force
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.log import get_logger
from framework.arc_loader import load_arc_config
from framework.chapter_coordinator import ChapterCoordinator

log = get_logger("run_chapter")


def main():
    parser = argparse.ArgumentParser(
        description="顶层协调器：分发章节管线（gen / engine / hybrid）")
    parser.add_argument("novel_dir", help="内容包目录（novels/<小说>/）")
    parser.add_argument("--chapter", type=int, required=True, help="章节号")
    parser.add_argument("--mode", choices=["gen", "engine", "hybrid"],
                        default=None, help="管线模式（缺省走 novel_config 分层）")
    parser.add_argument("--spec", default=None, help="gen 模式手写 spec 路径")
    parser.add_argument("--arc", default=None,
                        help="弧配置文件（markdown YAML frontmatter）")
    parser.add_argument("--force", action="store_true", help="跳过 spec 校验")
    args = parser.parse_args()

    arc_config = {}
    if args.arc:
        arc_config = load_arc_config(args.arc, novel_dir=args.novel_dir)

    coord = ChapterCoordinator(args.novel_dir)
    result = coord.run(args.chapter, spec_path=args.spec,
                       arc_config=arc_config, mode=args.mode, force=args.force)

    print(f"\n[coordinator] mode={result.mode} ch{result.chapter_num}")
    if result.tick_path:
        print(f"  tick:   {result.tick_path}")
    if result.spec_path:
        print(f"  spec:   {result.spec_path}")
    if result.output_path:
        print(f"  output: {result.output_path}")
    if result.type_a_events:
        print(f"  A 型确认: {len(result.type_a_events)} 条")
        for ev in result.type_a_events:
            print(f"    - {ev['fact_id']} {ev['proposition'][:40]}")
    if result.info_gaps:
        print(f"  信息差: {len(result.info_gaps.get('per_character', []))} 角色标注")


if __name__ == "__main__":
    main()
