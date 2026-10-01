#!/usr/bin/env python3
"""bench.calib.real_text — 判定器在**真实文本**上的校准（W5 的机械半壁）。

问题：判定器此前只在合成宇宙上校准过。合成宇宙是模板生成的，句式规整、
词汇受控——真实长篇不长那样。那么判定器换到真实文本上还准不准？

方法：**正例注入 + 诱饵对照**，不需要人工标注：

  对每一类判据，把已知的「真违反」和已知的「不该报」的孪生句分别插进
  真实文本，然后看判定器：
    · 正例必须被抓到        → 召回（recall）
    · 诱饵必须不被抓到      → 特异性（specificity）—— 这就是真实文本上的误报探针

  孪生句是关键：两句只差一个词（「祁三走进来」vs「祁三的名字写在册子上」、
  「第1天」vs「第二天」），载体文本、位置、长度全一样。于是「抓到 / 没抓到」
  只可能是判据本身的差别，不是文本差异造成的。

为什么不用人工标注：标注的是一句话该不该报，而这里我们**构造**了答案——
真值来自注入，不来自判断。人工标注留给 W5 的语义判定层（知识边界那种
没法机械构造的）。

诚实边界：
- 这套方法只覆盖**可注入**的判据（状态/人称/禁词/篇幅）。账本级的判据
  （时间线倒退、物品双持有）由 spec 声明驱动，正文注入触发不了，故不在此列。
- 真实文本上还会出现「既非注入、也非诱饵」的判定输出——那些是**候选**：
  可能是作者本人的疏漏，也可能是误报，机器分不出来。报告里如实列为
  「待人工复核」，不并入误报率。

用法：
    python3 -m bench.calib.real_text <章节目录或单个文件>... [--json out.json]
"""

import json
import re
from pathlib import Path

from bench.judges.mechanical import MechanicalRules, mechanical_judge
from bench.judges.state_judge import state_judge
from bench.state.ledger import build_ledger

# 注入用的探针角色：真实文本里几乎不会出现的名字，避免与原文人物混淆
PROBE_DEAD = "祁三"
PROBE_ITEM = "潮位尺"
PROBE_FORBIDDEN = "忽然"
PROBE_PROTAGONIST = "苏茜"      # 用于人称判据的「主角」设定（不参与注入）


def _spec(chapter: int, anchors, delta=None, desc="") -> dict:
    s = {"novel": "CALIB", "title": f"第{chapter}章", "chapter": chapter,
         "sections": [{"id": str(i + 1), "subject": "s", "scene_anchor": a,
                       "description": desc if i == 0 else ""}
                      for i, a in enumerate(anchors)]}
    if delta:
        s["state_delta"] = delta
    return s


def calibration_ledger():
    """探针账本：祁三在第 1 章死亡；第 2 章锚点为第 6 天。"""
    return build_ledger("CALIB", [
        _spec(1, ["第5天上午 @码头"], delta={"deaths": [PROBE_DEAD]}),
        _spec(2, ["第6天上午 @货棚"]),
    ], cast_names=[PROBE_DEAD, PROBE_PROTAGONIST], protagonist=PROBE_PROTAGONIST)


def calibration_rules() -> MechanicalRules:
    return MechanicalRules(forbidden_words=[PROBE_FORBIDDEN], target_chars=0,
                           para_max_chars=100, pov="third_limited",
                           protagonist=PROBE_PROTAGONIST,
                           protagonist_pronoun="她", cast_genders={})


# ── 注入器：每类一对「正例 / 诱饵」────────────────────────────────────

def _splice(text: str, sentence: str, pos: float = 0.5) -> str:
    """把句子插到文本中段的一个段落边界后（保持段落结构，避免制造超长段）。"""
    lines = text.split("\n")
    idx = max(1, int(len(lines) * pos))
    lines.insert(idx, sentence)
    return "\n".join(lines)


PROBES = [
    {   # 死人复活：活动 vs 遗物/回忆指称
        "kind": "state-dead",
        "what": "已死角色在正文中活动 / 被遗物性提及",
        "judge": "state",
        "positive": f"{PROBE_DEAD}走进来，把湿透的伞靠在门边。",
        "decoy": f"{PROBE_DEAD}的名字写在册子最后一页，字是墨写的。",
        "probe_prefix": "state-dead",
    },
    {   # 天数倒退：绝对天数 vs 相对天数惯用式
        "kind": "state-day",
        "what": "正文天数早于本章锚点 / 相对天数惯用式",
        "judge": "state",
        "positive": "第1天，他们还在码头上等潮。",
        "decoy": "第二天，他们还在码头上等潮。",
        "probe_prefix": "state-day",
    },
    {   # 视角越界：句首第一人称 vs 引语内的第一人称
        "kind": "cons-pov",
        "what": "第三人称旁白出现第一人称 / 引语内的第一人称",
        "judge": "mechanical",
        "positive": "我蹲进凹陷，等着上面的脚步过去。",
        "decoy": "她低声说：“我没听说有这回事。”",
        "probe_prefix": "cons-pov",
    },
    {   # 禁词：旁白里的禁词（禁词不区分旁白/引语，两侧都该抓）
        "kind": "cons-forbidden",
        "what": "禁词出现在正文",
        "judge": "mechanical",
        "positive": f"她{PROBE_FORBIDDEN}停住，回头看了一眼。",
        "decoy": None,      # 该类无诱饵：禁词判据不区分语境，没有「不该报」的孪生句
        "probe_prefix": "cons-forbidden",
    },
    {   # 篇幅：超长段落 vs 正常段落
        "kind": "cons-para",
        "what": "单段超过阈值 / 正常长度段落",
        "judge": "mechanical",
        "positive": "她数着木板缝，" + "一" * 120 + "。",
        "decoy": "她数着木板缝，一直数到门开。",
        "probe_prefix": "cons-para",
    },
]


def _judge_all(text: str, ledger, rules, chapter: int, run_id: str) -> list:
    out = mechanical_judge(text, rules, run_id, chapter)
    out += state_judge(text, ledger, chapter=chapter, run_id=run_id)
    return out


def _hits(vs: list, prefix: str, extra: str = "") -> list:
    return [v for v in vs if v.probe_id.startswith(prefix)]


def _signal(vs: list, prefix: str) -> int:
    """判据的**信号量**：命中条数不够用——禁词判据按章聚合（一章内同一禁词算一条），
    若原文已有该词，再注入一次不增加条数，会被误读成「没抓到」。

    所以取证据里的出现次数之和（`evidence["count"]`，缺省 1）。这样注入的
    每一次出现都能被看见，与聚合口径无关。
    """
    total = 0
    for v in _hits(vs, prefix):
        try:
            total += int((v.evidence or {}).get("count", 1))
        except (TypeError, ValueError):
            total += 1
    return total


def calibrate_texts(texts: list, chapter: int = 2) -> dict:
    """texts: [(名字, 正文)]；返回逐文本 × 逐判据的召回/特异性矩阵。"""
    ledger = calibration_ledger()
    rules = calibration_rules()
    rows = []
    for name, text in texts:
        # **差分测量**：真实文本自身就可能命中某些判据（例：真小说的段落本就
        # 超过合成的 100 字段落阈值）。所以基线取「干净文本的命中数」，
        # 只把「注入后相对基线的增量」算作判据的反应——否则真实文本的固有命中
        # 会被误记成「诱饵也被抓」，得出特异性 0% 的假结论。
        clean = _judge_all(text, ledger, rules, chapter, run_id=f"calib:{name}")
        base = {p["probe_prefix"]: _signal(clean, p["probe_prefix"]) for p in PROBES}
        row = {"name": name, "chars": len(text.strip()),
               "clean_candidates": len(clean), "clean_by_kind": base, "probes": {}}
        for p in PROBES:
            pref = p["probe_prefix"]
            pos_vs = _judge_all(_splice(text, p["positive"]), ledger, rules,
                                chapter, f"calib:{name}:pos")
            hit_pos = _signal(pos_vs, pref) > base[pref]
            dec = None
            if p["decoy"]:
                dec_vs = _judge_all(_splice(text, p["decoy"]), ledger, rules,
                                    chapter, f"calib:{name}:dec")
                dec = _signal(dec_vs, pref) == base[pref]
            row["probes"][p["kind"]] = {"recall": hit_pos, "specificity": dec,
                                        "baseline_hits": base[pref]}
        rows.append(row)
    return {"chapter": chapter, "rows": rows}


def summarize(result: dict) -> dict:
    """逐判据汇总：召回率、特异性（诱饵未误报的比例）。"""
    kinds = {}
    for p in PROBES:
        k = p["kind"]
        rec = [r["probes"][k]["recall"] for r in result["rows"]]
        spec = [r["probes"][k]["specificity"] for r in result["rows"]
                if r["probes"][k]["specificity"] is not None]
        kinds[k] = {
            "what": p["what"],
            "recall": round(sum(rec) / len(rec), 3) if rec else None,
            "specificity": round(sum(spec) / len(spec), 3) if spec else None,
            "n": len(rec),
        }
    chars = sum(r["chars"] for r in result["rows"])
    cand = sum(r["clean_candidates"] for r in result["rows"])
    baseline = {}
    for p in PROBES:
        k = p["probe_prefix"]
        baseline[k] = sum(r["clean_by_kind"].get(k, 0) for r in result["rows"])
    return {"kinds": kinds, "texts": len(result["rows"]), "chars": chars,
            "clean_candidates": cand, "clean_by_kind": baseline,
            "candidates_per_10k": round(cand / chars * 10000, 2) if chars else 0.0}


def load_corpus(paths) -> list:
    """目录（取 *.md，跳过状态/manifest 文件）或单文件 → [(名字, 正文)]。"""
    out = []
    for p in paths:
        path = Path(p).expanduser()
        files = sorted(path.glob("*.md")) if path.is_dir() else [path]
        for f in files:
            if any(s in f.name for s in ("状态", "manifest", "_revisions", ".ai.")):
                continue
            t = f.read_text(encoding="utf-8", errors="ignore").strip()
            if len(t) >= 400:            # 太短的样本注入后失真，跳过
                out.append((f.name, t))
    return out


def render_markdown(summary: dict, result: dict, corpus_label: str) -> str:
    L = [f"# 判定器真实文本校准 · {corpus_label}", "",
         f"样本：{summary['texts']} 段正文，共 {summary['chars']} 字；"
         f"注入正例/诱饵各 {summary['texts']} 次/类。", "",
         "方法：把「已知真违反」与「不该报的孪生句」分别插进真实文本"
         "（两句只差一个词，载体与位置相同），看判定器的反应。"
         "真值来自注入，不来自人工判断。", "",
         "| 判据 | 测什么 | 召回（正例被抓） | 特异性（诱饵未误报） |",
         "|---|---|---|---|"]
    for k, v in summary["kinds"].items():
        spec = "—（该类无诱饵）" if v["specificity"] is None else f"{v['specificity']:.0%}"
        L.append(f"| `{k}` | {v['what']} | {v['recall']:.0%} | {spec} |")
    L += ["", "真实文本自身的固有命中（未经任何注入）：", "",
          "| 判据 | 固有命中 | 说明 |", "|---|---|---|"]
    for k, n in summary["clean_by_kind"].items():
        note = "真实小说的段落长度本就超合成的口径" if k == "cons-para" else ""
        L.append(f"| `{k}` | {n} | {note} |")
    L += ["", f"**候选（非注入、非诱饵）：{summary['clean_candidates']} 条，"
              f"密度 {summary['candidates_per_10k']}/万字**——这些机器分不出是作者疏漏还是误报，"
              "需人工复核，故不计入误报率。", ""]
    return "\n".join(L) + "\n"


def main():
    import argparse
    ap = argparse.ArgumentParser(description="判定器真实文本校准")
    ap.add_argument("paths", nargs="+", help="章节目录或文件（可多个）")
    ap.add_argument("--label", default="未命名语料")
    ap.add_argument("--chapter", type=int, default=2, help="按第几章的规则判（默认 2）")
    ap.add_argument("--json", default="", help="结果 JSON 输出路径")
    ap.add_argument("--md", default="", help="报告 Markdown 输出路径")
    a = ap.parse_args()
    texts = load_corpus(a.paths)
    if not texts:
        raise SystemExit("没有可用样本（需 ≥400 字的 .md 正文）")
    res = calibrate_texts(texts, chapter=a.chapter)
    summ = summarize(res)
    md = render_markdown(summ, res, a.label)
    print(md)
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summ, "detail": res},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")
        print(f"→ {a.md}")


if __name__ == "__main__":
    main()
