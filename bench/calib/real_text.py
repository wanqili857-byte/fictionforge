#!/usr/bin/env python3
"""bench.calib.real_text — 判定器在**真实文本**上的校准（W5 的机械半壁）。

问题：判定器此前只在合成宇宙上校准过。合成宇宙是模板生成的，句式规整、
词汇受控——真实长篇不长那样。那么判定器换到真实文本上还准不准？

方法：**正例注入 + 诱饵对照**，不需要人工标注：

  对每一类判据，把已知的「真违反」和已知的「不该报」的孪生句分别插进
  真实文本，然后看判定器：
    · 正例必须被抓到        → 召回（recall）
    · 诱饵必须不被抓到      → 特异性（specificity）—— 这就是真实文本上的误报探针

  成对句是关键：**同载体、同位置、同插入长度**，差异只可能来自被插入的那句本身。
  注意别把这套说成「两句只差一个词」——那只对天数那一对成立（`第1天`/`第二天`）；
  死人复活那一对差的是**谓语类型**（「祁三走进来」= 活动 vs「祁三的名字写在册子上」
  = 遗物指称），句子长度也不同。措辞说强了，方法就成了它撑不住的样子（第二轮评审）。

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


# 中性对照：与被注入句**形状相同**（同样插在同一位置、同样带标点与长度）
# 但不含任何违反。它的存在是为了回答一个反驳：注入带来的增量会不会只是
# 「多插了一句话」造成的？中性句不产生增量，才说明增量来自违反本身（评审 F5）。
NEUTRAL_SENTENCE = "她把绳子在桩上绕了两圈，又解开。"

PROBES = [
    {   # 死人复活：活动 vs 遗物/回忆指称
        "kind": "state-dead",
        "what": "已死角色在正文中活动 / 被遗物性提及",
        "judge": "state",
        "positive": f"{PROBE_DEAD}走进来，把湿透的伞靠在门边。",
        "decoy": f"{PROBE_DEAD}的名字写在册子最后一页，字是墨写的。",
        "probe_prefix": "state-dead",
        # 该类要能测，前提是**死者名在语料里出现**——否则「零命中」是构造决定的
        "needs_entity_in_corpus": True,
    },
    {   # 天数倒退：绝对天数 vs 相对天数惯用式
        "kind": "state-day",
        "what": "正文天数早于本章锚点 / 相对天数惯用式",
        "judge": "state",
        "positive": "第1天，他们还在码头上等潮。",
        "decoy": "第二天，他们还在码头上等潮。",
        "probe_prefix": "state-day",
        "needs_entity_in_corpus": False,
    },
    {   # 视角越界：句首第一人称 vs 引语内的第一人称
        "kind": "cons-pov",
        "what": "第三人称旁白出现第一人称 / 引语内的第一人称",
        "judge": "mechanical",
        "positive": "我蹲进凹陷，等着上面的脚步过去。",
        "decoy": "她低声说：“我没听说有这回事。”",
        # 第二个正例专测 m0.3.0 修的那条：行首引号之后的旁白也要判。
        # 旧实现（整行跳过）对这句零命中，新实现必须命中——否则这次修复
        # 在真实文本上没有任何回归保护（评审 F12）。
        "positive_extra": "“这是我的。”我蹲进凹陷，等着上面的脚步过去。",
        "probe_prefix": "cons-pov",
        "needs_entity_in_corpus": False,
    },
    {   # 禁词：旁白里的禁词（禁词不区分旁白/引语，两侧都该抓）
        "kind": "cons-forbidden",
        "what": "禁词出现在正文",
        "judge": "mechanical",
        "positive": f"她{PROBE_FORBIDDEN}停住，回头看了一眼。",
        "decoy": None,      # 该类无诱饵：禁词判据不区分语境，没有「不该报」的孪生句
        "probe_prefix": "cons-forbidden",
        "needs_entity_in_corpus": False,
    },
    {   # 篇幅：超长段落 vs 正常段落
        "kind": "cons-para",
        "what": "单段超过阈值 / 正常长度段落",
        "judge": "mechanical",
        "positive": "她数着木板缝，" + "一" * 120 + "。",
        "decoy": "她数着木板缝，一直数到门开。",
        "probe_prefix": "cons-para",
        "needs_entity_in_corpus": False,
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


_NAME_STOP = ("他", "她", "它", "被", "的", "了", "着", "是", "有", "和", "与")
_NAME_RE = re.compile(r"([\u4e00-\u9fa5]{2,3})(?=[说道问答看走站坐喊叫笑])")


def guess_names(text: str, top: int = 1) -> list:
    """从语料里猜出真实出场的人名（正对照用）。

    粗糙但够用：取「两三个汉字 + 言语/动作动词」的高频片段，滤掉代词与虚词开头。
    正对照只需要一个人名——**名字必须真的在语料里出现**，否则对照本身又是空测。
    """
    from collections import Counter
    c = Counter(_NAME_RE.findall(text))
    out = []
    for name, _ in c.most_common(40):
        if name[0] in _NAME_STOP or name[-1] in _NAME_STOP:
            continue
        if len(set(name)) == 1:
            continue
        out.append(name)
        if len(out) >= top:
            break
    return out


def positive_control(text: str, rules, chapter: int = 2, run_id: str = "pc") -> dict:
    """**正对照**：把语料里真实出场的角色声明为死者，判据必须开火。

    为什么必须有它：若探针死者名在语料里根本不出现，「干净文本上零命中」是
    构造决定的，与判据好坏无关（评审 F3/F4 指出这是空测）。正对照把同一份
    真实文本配上**能命中的前提**，如果它也是零命中，才说明判据在该负载上失灵。
    """
    names = guess_names(text)
    if not names:
        return {"name": None, "hits": None, "note": "语料里没猜出人名，正对照不可用"}
    name = names[0]
    led = build_ledger("CALIB-PC", [
        _spec(1, ["第5天上午 @码头"], delta={"deaths": [name]}),
        _spec(2, ["第6天上午 @货棚"]),
    ], cast_names=[name, PROBE_PROTAGONIST], protagonist=PROBE_PROTAGONIST)
    vs = _judge_all(text, led, rules, chapter, run_id)
    hits = _hits(vs, "state-dead")
    return {"name": name, "hits": len(hits),
            "spans": [v.evidence.get("span", "")[:40] for v in hits[:3]]}


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
        # 中性对照：同形状的无害句，增量必须为 0
        neutral = _judge_all(_splice(text, NEUTRAL_SENTENCE), ledger, rules,
                             chapter, f"calib:{name}:neutral")
        neutral_delta = sum(_signal(neutral, p["probe_prefix"]) for p in PROBES) - sum(base.values())
        row = {"name": name, "chars": len(text.strip()),
               "clean_candidates": len(clean), "clean_by_kind": base,
               "neutral_delta": neutral_delta,
               "probe_entity_in_corpus": PROBE_DEAD in text,
               "positive_control": positive_control(text, rules, chapter, f"calib:{name}:pc"),
               "probes": {}}
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
            extra = None
            if p.get("positive_extra"):
                ex_vs = _judge_all(_splice(text, p["positive_extra"]), ledger, rules,
                                   chapter, f"calib:{name}:extra")
                extra = _signal(ex_vs, pref) > base[pref]
            row["probes"][p["kind"]] = {"recall": hit_pos, "specificity": dec,
                                        "recall_extra": extra,
                                        "baseline_hits": base[pref],
                                        # 该类要能测，前提是探针实体在语料里出现；
                                        # 否则「注入前零命中」是构造决定的，不是证据。
                                        # 这个布尔值由 summarize 用来算「有几段文本的
                                        # 基线是证据」——**别再让它只声明不读**
                                        # （第二轮评审：needs_entity_in_corpus 声明 5 处、读 0 处）。
                                        "needs_entity": p["needs_entity_in_corpus"],
                                        "baseline_is_evidence":
                                            (not p["needs_entity_in_corpus"]
                                             or PROBE_DEAD in text)}
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
        ev = sum(1 for r in result["rows"]
                 if r["probes"][k]["baseline_is_evidence"])
        kinds[k] = {
            "what": p["what"],
            "recall": round(sum(rec) / len(rec), 3) if rec else None,
            "specificity": round(sum(spec) / len(spec), 3) if spec else None,
            "n": len(rec),
            # 基线是证据的文本数。needs_entity 类若少于 n，「注入前零命中」
            # 有一部分只是构造决定的——读结论时必须看这一列。
            "baseline_evidence_n": ev,
            "needs_entity_in_corpus": p["needs_entity_in_corpus"],
        }
    # 「干净文本零命中」只在探针实体真的出现在语料里时才算证据（评审 F3）
    measurable = [r for r in result["rows"] if r["probe_entity_in_corpus"]]
    pcs = [r["positive_control"] for r in result["rows"]
           if r["positive_control"]["hits"] is not None]
    chars = sum(r["chars"] for r in result["rows"])
    cand = sum(r["clean_candidates"] for r in result["rows"])
    baseline = {}
    for p in PROBES:
        k = p["probe_prefix"]
        baseline[k] = sum(r["clean_by_kind"].get(k, 0) for r in result["rows"])
    return {"kinds": kinds, "texts": len(result["rows"]), "chars": chars,
            "clean_candidates": cand, "clean_by_kind": baseline,
            "candidates_per_10k": round(cand / chars * 10000, 2) if chars else 0.0,
            "state_dead_measurable_texts": len(measurable),
            "neutral_delta_total": sum(r["neutral_delta"] for r in result["rows"]),
            "positive_control": {
                "texts_with_hits": sum(1 for p in pcs if p["hits"] > 0),
                "texts_measured": len(pcs),
                "example": pcs[0] if pcs else None,
            }}


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
         "方法：把「已知真违反」与**不该报的成对句**分别插进真实文本"
         "（同载体、同位置、同插入长度），看判定器的反应。"
         "真值来自注入，不来自人工判断。", "",
         "> 措辞订正：不写成「两句只差一个词」——那只对天数那一对成立。"
         "死人复活那一对差的是谓语类型（活动 vs 遗物指称），长度也不同。"
         "保证来自「同载体、同位置、同插入长度」，不是来自字面相似。", "",
         "| 判据 | 测什么 | 召回（正例被抓） | 特异性（诱饵未误报） | 基线是证据的文本 |",
         "|---|---|---|---|---|"]
    for k, v in summary["kinds"].items():
        spec = "—（该类无诱饵）" if v["specificity"] is None else f"{v['specificity']:.0%}"
        ev = v.get("baseline_evidence_n")
        evt = "—" if ev is None else (f"{ev}/{v['n']}" if v.get("needs_entity_in_corpus")
                                      else f"{v['n']}/{v['n']}")
        L.append(f"| `{k}` | {v['what']} | {v['recall']:.0%} | {spec} | {evt} |")
    L += ["", "真实文本自身的固有命中（未经任何注入）：", "",
          "| 判据 | 固有命中 | 说明 |", "|---|---|---|"]
    for k, n in summary["clean_by_kind"].items():
        note = "真实小说的段落长度本就超合成的口径" if k == "cons-para" else ""
        L.append(f"| `{k}` | {n} | {note} |")
    pc = summary["positive_control"]
    L += ["", "## 三项对照（回答「这份校准凭什么算数」）", "",
          "| 对照 | 结果 | 它排除了什么 |",
          "|---|---|---|",
          f"| **中性注入**（同位置插一句无害句） | 增量 **{summary['neutral_delta_total']}** | "
          "排除「增量只是多插了一句话造成的」——增量为 0，说明增量来自违反本身 |",
          f"| **正对照**（把语料里真实出场的角色声明为死者） | "
          f"{pc['texts_with_hits']}/{pc['texts_measured']} 段命中"
          + (f"（例：{pc['example']['name']} → {pc['example']['hits']} 条）"
             if pc.get("example") else "") + " | "
          "排除「判据在真实散文上根本不开火」——前提成立时它确实开火 |",
          f"| **可测性**（探针死者名是否出现在语料中） | "
          f"{summary['state_dead_measurable_texts']}/{summary['texts']} 段可测 | "
          "标明哪些「零命中」是证据、哪些只是构造决定的（不可测时标 n/a） |",
          "",
          f"**候选（非注入、非诱饵）：{summary['clean_candidates']} 条，"
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
