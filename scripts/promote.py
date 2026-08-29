#!/usr/bin/env python3
"""v0.3.0 修订感知管线 · promote：人工修订稿晋升为 canon。

AI 草稿（generated 落 chapters/drafts/）经人工修订后，本工具：
  1. diff(修订稿, AI 原稿快照 .ai.md) → 段落级改动 hunks
  2. LLM 逐 hunk 标注原因标签（AI味/节奏/素材/逻辑/结构/润色/弃用）
  3. 写 canon（chapters/第N章 X.md）——生成管线永不覆盖
  4. 写修订记录（chapters/_revisions/第N章 X.rev.json）——蒸馏回灌的原料
  5. 在 canon 上重提取章节状态（章节状态.md / 角色状态.json）
  6. canon 前门禁：anti-AI + verve 复查（报告，不自动改）

用法:
  python3 scripts/promote.py <novel_dir> --draft <草稿文件名>
  python3 scripts/promote.py <novel_dir> --chapter 1 --title "第1章 纸板"
  python3 scripts/promote.py <novel_dir> --chapter 1 --no-label   # 跳过 LLM 标注
"""
import argparse
import difflib
import json
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("promote")

LABELS = ["AI味", "节奏", "素材", "逻辑", "结构", "润色", "弃用"]

# 让 scripts/promote.py 可 import 同目录 gen.py + 项目根 server/、framework/
_SELF = Path(__file__).resolve()
sys.path.insert(0, str(_SELF.parent))
sys.path.insert(0, str(_SELF.parent.parent))  # 项目根（fictionforge/）


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── Diff ──────────────────────────────────────────────────────────────
def chunk_by_sentences(text: str) -> list[str]:
    """按句切块（保留换行段），供 diff 对齐。"""
    chunks = []
    for para in text.split("\n"):
        for sent in re.split(r"(?<=[。！？])", para):
            sent = sent.strip()
            if sent:
                chunks.append(sent)
    return chunks


def diff_sentences(ai_text: str, final_text: str) -> list[dict]:
    """返回改动 hunks：{old:[…], new:[…], kind}，kind ∈ replace/delete/insert。"""
    a = chunk_by_sentences(ai_text)
    b = chunk_by_sentences(final_text)
    sm = difflib.SequenceMatcher(None, a, b)
    hunks = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        hunks.append({
            "old": a[i1:i2],
            "new": b[j1:j2],
            "kind": "replace" if tag == "replace" else tag,
        })
    return hunks


# ── LLM 标注 ──────────────────────────────────────────────────────────
_LABEL_PROMPT = (
    "你是资深小说编辑的修订板记。下面是作者对 AI 生成草稿做的改动。"
    "对每一处改动，判断作者改它的原因，打上唯一的标签。\n"
    "标签只有这 7 种，各自含义：\n"
    "- AI味：原文是典型的 AI 腔（空泛/套路/不像人写的），作者改成了更像人的说法\n"
    "- 节奏：句长/段落/语气节奏的调整（太满或太平，作者松紧、呼吸）\n"
    "- 素材：作者补了具体细节/形貌/声音/氛围/生活质感（给了 AI 没有的素材）\n"
    "- 逻辑：情节/因果/设定上说不通，作者修掉了 bug\n"
    "- 结构：段落归属、顺序、场景边界的调整（含大段重写/调动）\n"
    "- 润色：小改词句，通顺、声音更好听，没有质变\n"
    "- 弃用：删掉一段（不要了），不是改写\n"
    "严格只输出 JSON 数组，不要 ``` 围栏，不要任何解释文字。每项："
    '{"old": "被改的原文片段（若有）", "new": "改后的文字（若为纯删除则空）", "label": "标签", "reason": "一句话为什么改"}'
)


def label_hunks(hunks: list[dict], route) -> list[dict]:
    """LLM 逐条标注；返回带 label/reason 的 hunks。失败回退：重试一次 → 正则兜底 → 全标润色。"""
    from gen import call_api

    if not hunks:
        return []
    lines = ["修订清单（序号: 原文 → 改后）：\n"]
    for i, h in enumerate(hunks, 1):
        old = " / ".join(h["old"][:6])
        new = " / ".join(h["new"][:6])
        verb = {"replace": "改", "delete": "删", "insert": "增"}.get(h["kind"], "改")
        lines.append(f"{i}. [{verb}] {old}  →  {new}")
    user_prompt = "\n".join(lines)

    raw = None
    for attempt in range(2):  # 网络/格式不稳定，重试一次
        try:
            raw = call_api(_LABEL_PROMPT, user_prompt, route, silent=True)
        except Exception as e:
            log.warning(f"[label] LLM 标注异常({attempt}): {e}")
            raw = None
        if raw:
            data = _extract_json_array(raw)
            if data is not None:
                return [_annotate(h, data) for h in hunks]
            if attempt == 0:
                log.warning("[label] LLM 返回非标准 JSON，重试一次")
    if raw:
        data = _scavenge_labels(raw)
        if data:
            return [_annotate(h, data) for h in hunks]
    log.warning("[label] LLM 标注失败，回退全标润色")
    return [{**h, "label": "润色", "reason": ""} for h in hunks]


def _annotate(h: dict, data: list) -> dict:
    label, reason = _match_label(h, data)
    return {**h, "label": label, "reason": reason}


def _scavenge_labels(raw: str) -> Optional[list]:
    """兜底：正则抓 `label":"X"`，尽量配对 old/new。失败返回 None。"""
    labels = re.findall(r'"label"\s*:\s*"([^"]+)"', raw)
    if not labels:
        return None
    olds = re.findall(r'"old"\s*:\s*"([^"]*)"', raw)
    news = re.findall(r'"new"\s*:\s*"([^"]*)"', raw)
    n = max(len(labels), len(olds), len(news))
    out = []
    for i in range(n):
        out.append({
            "old": olds[i] if i < len(olds) else "",
            "new": news[i] if i < len(news) else "",
            "label": labels[i] if i < len(labels) else "润色",
            "reason": "",
        })
    return out


def _extract_json_array(raw: str):
    """剥 code fence / 前后缀，取第一个 JSON 数组。失败返回 None。"""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _match_label(h: dict, data: list) -> tuple[str, str]:
    old = " / ".join(h["old"])
    new = " / ".join(h["new"])
    for d in data:
        if not isinstance(d, dict):
            continue
        do, dn = d.get("old", ""), d.get("new", "")
        if (h["old"] and do and (do in old or old in do)) or \
           (h["new"] and dn and (dn in new or new in dn)):
            label = d.get("label", "润色")
            return (label if label in LABELS else "润色"), d.get("reason", "")
    return "润色", ""


# ── 主流程 ────────────────────────────────────────────────────────────
def _locate(novel_dir: Path, chapter: Optional[int], title: Optional[str]):
    """返回 (draft, snapshot, canon, ch_num)。找不到则 sys.exit。"""
    drafts_dir = novel_dir / "chapters" / "drafts"
    if not drafts_dir.is_dir():
        sys.exit(f"ERROR: 无草稿目录 {drafts_dir}")

    if title:
        draft = drafts_dir / f"{title}.md"
        if not draft.exists():
            sys.exit(f"ERROR: 草稿不存在 {draft}")
    elif chapter:
        hits = [f for f in drafts_dir.iterdir()
                if f.is_file() and f.name.endswith(".md")
                and not f.name.endswith(".ai.md")
                and re.match(rf"第({chapter})章", f.name)]
        if len(hits) != 1:
            sys.exit(f"ERROR: 第{chapter}章草稿命中 {len(hits)} 个，用 --title 指定" if hits
                     else f"ERROR: 无第{chapter}章草稿")
        draft = hits[0]
    else:
        sys.exit("ERROR: 需要 --chapter 或 --title 之一")

    snapshot = draft.with_name(draft.stem + ".ai.md")
    canon = draft.parent.parent / draft.name
    m = re.match(r"第(\d+)章", draft.name)
    ch_num = int(m.group(1)) if m else (chapter or 0)
    return draft, snapshot if snapshot.exists() else None, canon, ch_num


def _load_spec(novel_dir: Path, ch_num: int):
    p = novel_dir / "specs" / f"ch{ch_num}.json"
    if p.exists():
        try:
            return _load_json(p)
        except Exception:
            pass
    return None


def _load_config(novel_dir: Path) -> dict:
    p = novel_dir / "novel_config.json"
    if p.exists():
        try:
            return _load_json(p)
        except Exception:
            pass
    return {}


def _gate_canon(text, spec, novel_dir, config):
    from gen import (anti_ai_check, load_adapt_rules, print_violations,
                     verve_review, print_verve_review)
    print("\n-- canon 前门禁（anti-AI + verve，只报告不自动改）--")
    try:
        rules = load_adapt_rules()
        violations = anti_ai_check(text, rules)
        print_violations(violations)
    except Exception as e:
        print(f"   [anti-AI] 跳过: {e}")
    try:
        findings = verve_review(text, spec, config)
        print_verve_review(findings)
    except Exception as e:
        print(f"   [verve] 跳过: {e}")


def _write_revision(novel_dir, draft, snapshot, canon, ch_num, annotated):
    rev_dir = draft.parent.parent / "_revisions"
    rev_dir.mkdir(parents=True, exist_ok=True)
    final_chars = len(canon.read_text(encoding="utf-8").rstrip("\n")) if canon.exists() else 0
    rec = {
        "novel": novel_dir.name,
        "chapter": ch_num,
        "title": draft.stem,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "ai_chars": len(snapshot.read_text(encoding="utf-8").rstrip("\n")) if snapshot else 0,
        "final_chars": final_chars,
        "hunk_count": len(annotated),
        "hunks": [
            {
                "kind": h["kind"], "label": h["label"], "reason": h.get("reason", ""),
                "old": " ".join(h["old"])[:300],
                "new": " ".join(h["new"])[:600],
            }
            for h in annotated
        ],
        "labels": {l: sum(1 for h in annotated if h["label"] == l) for l in LABELS},
    }
    rev = rev_dir / f"{draft.stem}.rev.json"
    rev.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   修订记录: {rev}")


def main():
    ap = argparse.ArgumentParser(description="AI 草稿 → 人工修订 → canon 晋升")
    ap.add_argument("novel_dir", help="内容包目录（如 novels/静默轨道，或作者本地私有包路径）")
    ap.add_argument("--chapter", type=int, default=None)
    ap.add_argument("--title", default=None, help="草稿名（不含 .md），如 '第1章 纸板'")
    ap.add_argument("--no-label", action="store_true", help="跳过 LLM 标注（全部记润色）")
    args = ap.parse_args()

    novel_dir = Path(args.novel_dir).resolve()
    draft, snapshot, canon, ch_num = _locate(novel_dir, args.chapter, args.title)

    final_text = draft.read_text(encoding="utf-8").rstrip("\n")
    ai_text = snapshot.read_text(encoding="utf-8").rstrip("\n") if snapshot else None

    print(f"\n== promote: {draft.name}")
    if snapshot is None:
        print("   无 AI 原稿快照（遗留草稿）——按全新增处理，只晋升 + 取状态")
    print(f"   修订稿 {len(final_text)} chars"
          + (f" | AI 原稿 {len(ai_text)} chars" if ai_text else ""))

    # diff + 标注
    hunks = diff_sentences(ai_text or "", final_text) if ai_text else []
    if args.no_label or not hunks:
        annotated = [{**h, "label": "润色", "reason": ""} for h in hunks]
    else:
        from server._api_client import MODEL_ROUTES
        annotated = label_hunks(hunks, MODEL_ROUTES["normal"])
    print(f"   改动 hunks: {len(annotated)}"
          + ("(--no-label 全记润色)" if args.no_label else ""))

    # spec / config
    spec = _load_spec(novel_dir, ch_num)
    if spec is None:
        print(f"   WARN: 无 specs/ch{ch_num}.json，状态/verve 用默认")
        spec = {"sections": []}
    config = _load_config(novel_dir)

    # canon 前门禁（先报告，再晋升）
    _gate_canon(final_text, spec, novel_dir, config)

    # 写 canon
    canon.parent.mkdir(parents=True, exist_ok=True)
    canon.write_text(final_text + "\n", encoding="utf-8")
    print(f"   canon 已写入 {canon}")

    # 状态重提取（从 canon）
    try:
        from gen import update_chapter_state
        update_chapter_state(str(novel_dir), ch_num, final_text, spec, config)
        print("   章节状态已重提取（章节状态.md）")
    except Exception as e:
        print(f"   WARN: 状态更新失败 {e}")

    # 修订记录
    _write_revision(novel_dir, draft, snapshot, canon, ch_num, annotated)

    # 报告
    c = Counter(h["label"] for h in annotated)
    print("\n-- 修订原因分布 --")
    for label in LABELS:
        if c[label]:
            print(f"   {label}: {c[label]}")
    print("\ncanon 晋升完成。下一步：scripts/distill.py（蒸馏进判例库，v0.3.0 待实现）。")


if __name__ == "__main__":
    main()