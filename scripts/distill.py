#!/usr/bin/env python3
"""v0.3.0 修订感知管线 · distill：修订记录蒸馏进判例库（素材库）。

读 chapters/_revisions/*.rev.json（promote 产物），提取作者人工改动里的
「好例」——只存人改后的句子（v5 教训：prompt 里任何句子都是例子，AI 原句
属于坏例，留在修订记录供人审，不注入 prompt）。

来源标签：
  - AI味（replace）：人把 AI 腔改成的更像人话的句子
  - 素材（replace/insert）：作者补的具体细节/形貌/声音/氛围

配额经济：新进旧出。**默认上限 20 条**（实测每条约 ~50 字，20 条 ≈ +1000 字注入；
总 prompt 应压在 ~3300 以内——宪法 ~2300 + 素材库 ~1000 是平衡点）。蒸馏后人工审校。

用法:
  python3 scripts/distill.py <novel_dir> [--max 40] [--min-len 12]
"""
import argparse
import json
import re
import sys
from pathlib import Path

_SELF = Path(__file__).resolve()
sys.path.insert(0, str(_SELF.parent))

HEADER = """# 素材库（判例库）

> 修订回灌蒸馏产物。自动从 `chapters/_revisions/` 生成，**新进旧出，上限 {max} 条**。
> 全部是作者人工改动后的句子/细节——模型该长同款触觉。
> 铁律：prompt 里任何句子都是例子，模型好坏照抄——这里只收好例，不收 AI 原句。
> 人工审校：蒸馏后通读一遍，删掉没普适性或依赖具体剧情的条目。
"""


def load_revisions(novel_dir: Path) -> list[dict]:
    rev_dir = novel_dir / "chapters" / "_revisions"
    if not rev_dir.is_dir():
        return []
    recs = []
    for f in sorted(rev_dir.glob("*.rev.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            recs.append(data)
        except Exception as e:
            print(f"   WARN: 跳过 {f.name}: {e}")
    recs.sort(key=lambda r: r.get("promoted_at", ""), reverse=True)  # 新的在前
    return recs


def collect_examples(recs: list[dict]) -> list[dict]:
    """从修订记录取好例：(text, source_tag)。

    修订 hunk 常是大段改写（人一口气重写整个场景），直接存整段会爆 prompt 预算。
    这里把 hunk 的 new 拆到**句级**，只留短句做好例——模型学的就是一句一句的触觉。
    """
    out = []
    seen = set()
    for rec in recs:
        ch_tag = f"ch{rec.get('chapter')}·{rec.get('title', '')}".strip("·")
        for h in rec.get("hunks", []):
            label = h.get("label", "")
            kind = h.get("kind", "")
            new = (h.get("new") or "").strip()
            if label == "AI味" and kind in ("replace",):
                pass
            elif label == "素材" and kind in ("replace", "insert"):
                pass
            else:
                continue
            for sent in re.split(r"(?<=[。！？”])", new):
                sent = sent.strip()
                if not sent:
                    continue
                if sent in seen:
                    continue
                seen.add(sent)
                out.append({"text": sent, "tag": f"{ch_tag}·{label}"})
    return out


def main():
    ap = argparse.ArgumentParser(description="修订记录 → 素材库判例")
    ap.add_argument("novel_dir")
    ap.add_argument("--max", type=int, default=20, help="配额上限，默认 20（≈+1000字注入）")
    ap.add_argument("--min-len", type=int, default=12, help="跳过短于该字符数的好例")
    args = ap.parse_args()

    novel_dir = Path(args.novel_dir).resolve()
    recs = load_revisions(novel_dir)
    if not recs:
        sys.exit(f"无修订记录 {novel_dir}/chapters/_revisions/——先跑 promote")

    examples = collect_examples(recs)
    examples = [e for e in examples
                if args.min_len <= len(e["text"]) <= 200]  # 短句好例，超长句不要

    n_total, n_kept = len(examples), min(len(examples), args.max)
    dropped = n_total - n_kept
    examples = examples[:args.max]  # 新在前，配额挤出旧的

    lines = [HEADER.format(max=args.max), "", "## 好例", ""]
    for e in examples:
        lines.append(f"- `[{e['tag']}]` {e['text']}")
    lines.append("")

    out_path = novel_dir / "bible" / "判例" / "素材库.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"== distill: {novel_dir.name}")
    print(f"   {len(recs)} 份修订记录 → {n_total} 个好句（句级，≤200字），"
          f"配额 {args.max} 保留，挤出 {dropped}")
    print(f"   已写入 {out_path}")
    print("   提示：请人工审校一次性读完，删掉没普适性的条目，再跑下一次生成。")


if __name__ == "__main__":
    main()