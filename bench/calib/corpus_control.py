#!/usr/bin/env python3
"""bench.calib.corpus_control — 状态判据在**入库语料**上的正/负对照（零 LLM）。

## 为什么需要这个模块

v2 的 9 个官方 run 在状态轴上**零命中**（60 条判决全是机械约束：
禁词 29 / 篇幅 22 / 段落 6 / 视角 3）。零命中在产物里可以读成两件事：

  a) 判据在这份语料上开不了火 → 状态轴是死的，榜单不该拿它当卖点
  b) 语料里确实没有状态违反 → 零命中是观测，轴是活的

**两件事在 `violations.json` 里长得一模一样**——这正是外部评审（codex 第二轮 F16）
指出的问题：一个自称测「长程一致性」的基准，入库产物里一条一致性违反都没有，
而读者无从分辨这是判据失效还是模型没问题。

`bench/calib/real_text.py` 校准的是判据在**别的**真实文本上的行为，
回答不了这个问题：它用的是探针账本（祁三），不是入库语料的那份账本。
本模块把判定器**放回入库语料 + 入库账本**上量：

* **正对照**：往官方章节注入真复活 / 真天数倒退 → 判据必须命中
* **负对照**：往同一位置注入孪生句（遗物指称 / 比喻框）→ 判据**不得**多报
* **基线**：注入前的命中数（差分测量的底）

## 与 real_text 的分工

| | real_text | corpus_control |
|---|---|---|
| 语料 | 作者的真实长篇 / 示例 | **入库的官方跑批产物** |
| 账本 | 探针账本（构造） | 宇宙账本（seed/chapters 决定的真账本） |
| 回答 | 判据换负载还准吗 | **榜单的零命中是观测还是失效** |

## 已知边界（不掩饰）

- 只覆盖**可注入**的判据（`state-dead` / `state-day`）。
  `state-timeline` / `state-item` 由账本自身驱动，插句子触发不了——
  它们的对照在 `generator.invariants()`（那是宇宙自检，不是模型判定）。
- 注入用的是**真死者名**（从宇宙账本取），不是探针名。这是刻意的：
  语料里没出现过的名字，零命中是构造决定的（real_text 的教训）。
- 正例模板只有几条。**每条模板的召回率单独列出**，因为活动动词表是封闭的
  （34 词），召回随句式变化——把这件事量出来，比写一句「可能有漏报」有用。

用法：
    python3 -m bench.calib.corpus_control bench/results/v2            # 人读
    python3 -m bench.calib.corpus_control bench/results/v2 --json x.json --md y.md
"""

import argparse
import glob
import json
import os
import sys

from bench.judges.state_judge import state_judge
from bench.state.ledger import build_ledger
from bench.universe.generator import generate

# ── 正例模板：同一个真复活，换活动动词 / 句式 ────────────────────────
# 分母固定，按模板分别统计召回——动词表封闭带来的漏报会直接显示成某一行的 0。
DEAD_POSITIVE_TEMPLATES = [
    ("{n}走过来，把湿透的伞靠在门边。", "位移+持物"),
    ("{n}伸手接过盐箱。", "手部动作"),
    ("{n}站在门口，没说话。", "姿态动词"),
    ("{n}开口，声音比从前哑。", "言语动词"),
    # 下面两条是**已知取舍**的量化：句内出现比喻词 / 过去时标记时，整句被豁免，
    # 于是一个真复活被漏掉（PAST_MARKERS 宽带）。docstring 原先只承认「比喻/传闻」
    # 一类，第二轮评审指出 当时/以前/当年/曾经 同属整句豁免——把它们放进对照，
    # 让取舍的代价是一个**数**，不是一句「可能有漏报」。
    #
    # 注意正例必须**无歧义**：这两句的**动作都发生在当下**，标记词只修饰别的东西。
    # 反面例子：「{n}当时也在码头上，把盐箱搬上跳板。」——「当时」直接限定该动作，
    # 整句本就是在叙述过去的一刻，豁免它是**对的**。拿它当正例等于把自己的误标
    # 当成 ground truth（本项目栽过四次的恒真夹具，就是这个形状）。
    ("{n}走进来，像往常一样把伞靠在门边。", "比喻框包裹的真复活"),
    ("{n}走进来，说起了当年的事。", "过去时标记包裹的真复活"),
]

# 负例模板：名字出现但在**纪念/比喻/从句**语境里，不是复活。判据必须不报。
DEAD_NEGATIVE_TEMPLATES = [
    ("{n}的名字写在册子最后一页，字是墨写的。", "遗物指称"),
    ("她想起{n}当时蹲在跳板上。", "回忆"),
    ("就像三年前站在跳板中间的{n}那样，雾又漫上来了。", "比喻框"),
    # 关系从句的中心语：豁免正则只认 `(过|了)?的`，不认 `着+的`——
    # 第二轮评审指出「死者+动词+着+的+名词」会漏过豁免被当成复活。
    ("{n}蹲着的那块跳板，早就烂了。", "着+的关系从句"),
    # 下面三条是**入库语料上真实出现过的形态**，在收窄软标记豁免的那一版里
    # 全部误报过（clean_hits 3）。加进来当回归夹具：它们必须一直不报。
    ("{n}当年也走到过这一步，也被堵在干船坞。", "闪回（名后时间副词）"),
    ("{n}当时蹲在这，摸着栈桥的木板。", "闪回（名后「当时」）"),
    ("{n}没走完的路，现在轮到她走了。", "关系从句（动词+补语+的）"),
]

DAY_POSITIVE = ("第1天，他们还在码头上等潮。", "绝对天数早于锚点")
DAY_NEGATIVE = ("第二天，他们还在码头上等潮。", "相对天数惯用式")


def universe_state(seed=42, chapters=6, protagonist="苏茜"):
    """返回 (宇宙, 账本, 死亡章 {角色: 章号})——判据要用的那份真账本。"""
    u = generate(seed=seed, chapters=chapters)
    names = [c["name"] for c in u.cast]
    led = build_ledger(u.title, u.specs, names, protagonist=protagonist)
    deaths = {}
    for sp in u.specs:
        for nm in (sp.get("state_delta") or {}).get("deaths", []):
            deaths[nm] = sp["chapter"]
    return u, led, deaths


def _hits(text, ledger, chapter):
    """该文本在该章触发的 probe_id **计数**（差分测量用计数，不用集合）。

    这里踩过一个坑：probe_id 是 `state-dead-ch{章}`，**同一章内多条命中同一个 id**。
    用「集合是否变大」当判据，则一章若已有一条命中，后续注入永远看不到新 id，
    全部被记成漏报——实测把 6 个模板一起从 36/36 压成 34/36 的假象。
    正确口径是**计数增量**（与 real_text 里禁词按章聚合的那个坑同源）。
    """
    from collections import Counter
    return Counter(v.probe_id for v in state_judge(text, ledger, chapter=chapter,
                                                   run_id="corpus-control"))


def _fired(before, after, prefix):
    """注入后相对注入前，`prefix` 类命中数是否**增加**。"""
    return sum(n for k, n in after.items() if k.startswith(prefix)) > \
           sum(n for k, n in before.items() if k.startswith(prefix))


def control_one(text, ledger, chapter, deaths, dead_name):
    """单章对照：返回该章每种注入的增量命中。

    差分测量：只把「注入后相对干净文本**新增**的 probe_id」算作判据反应。
    真实文本自带的命中会淹没反应——这个坑在 real_text 里已经踩过一次。
    """
    clean = _hits(text, ledger, chapter)
    row = {"chapter": chapter, "clean_hits": sum(clean.values()),
           "clean_spans": sorted(v.probe_id for v in
                                 state_judge(text, ledger, chapter=chapter,
                                             run_id="corpus-control")),
           "positive": {}, "negative": {}}
    for tpl, label in DEAD_POSITIVE_TEMPLATES:
        if not dead_name:
            row["positive"][label] = None      # 该章无可注入的死者 → 不计入
            continue
        after = _hits(text + "\n" + tpl.format(n=dead_name), ledger, chapter)
        row["positive"][label] = _fired(clean, after, "state-dead")
    for tpl, label in DEAD_NEGATIVE_TEMPLATES:
        if not dead_name:
            row["negative"][label] = None
            continue
        after = _hits(text + "\n" + tpl.format(n=dead_name), ledger, chapter)
        row["negative"][label] = _fired(clean, after, "state-dead")
    # 天数对照：只在锚点天 > 1 的章上做（否则「第1天」不是倒退）
    cur = ledger.chapter(chapter)
    if cur is not None and cur.day and cur.day > 1:
        for tpl, label in (DAY_POSITIVE, DAY_NEGATIVE):
            after = _hits(text + "\n" + tpl, ledger, chapter)
            key = "positive" if (tpl, label) == DAY_POSITIVE else "negative"
            row[key][label] = _fired(clean, after, "state-day")
    return row


def run_control(runs_dir="bench/results/v2", seed=42, chapters=6):
    """对入库语料跑一遍正/负对照。返回逐 run 逐章结果 + 汇总。"""
    u, led, deaths = universe_state(seed=seed, chapters=chapters)
    out = {"runs_dir": runs_dir, "seed": seed, "chapters": chapters,
           "universe": u.title, "deaths": deaths, "rows": []}
    for run_dir in sorted(glob.glob(os.path.join(runs_dir, "*"))):
        if not os.path.isdir(run_dir):
            continue
        run = os.path.basename(run_dir.rstrip("/"))
        for ch in range(1, chapters + 1):
            p = os.path.join(run_dir, f"ch{ch}.md")
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                text = f.read()
            # 本章之前死掉的角色，才可能在本章复活
            dead = [n for n, dch in sorted(deaths.items()) if dch < ch]
            row = control_one(text, led, ch, deaths, dead[0] if dead else None)
            row["run"] = run
            row["dead_injectable"] = dead[0] if dead else None
            out["rows"].append(row)
    out["summary"] = summarize(out)
    return out


def summarize(res):
    """汇总：**按注入模板分行**报召回，别把几种句式糊成一个数。"""
    pos, neg = {}, {}
    base = 0
    for r in res["rows"]:
        base += r["clean_hits"]
        for label, v in r["positive"].items():
            if v is None:
                continue
            d = pos.setdefault(label, {"hit": 0, "n": 0})
            d["n"] += 1
            d["hit"] += 1 if v else 0
        for label, v in r["negative"].items():
            if v is None:
                continue
            d = neg.setdefault(label, {"hit": 0, "n": 0})
            d["n"] += 1
            d["hit"] += 1 if v else 0
    return {
        "run_chapters": len(res["rows"]),
        "clean_state_hits": base,
        "positive": {k: {"recall": (v["hit"] / v["n"] if v["n"] else None),
                         "hit": v["hit"], "n": v["n"]} for k, v in pos.items()},
        "negative": {k: {"false_fire": (v["hit"] / v["n"] if v["n"] else None),
                         "hit": v["hit"], "n": v["n"]} for k, v in neg.items()},
    }


def verdict(res):
    """一句结论：零命中是**观测**还是**失效**。这是本模块存在的理由。"""
    s = res["summary"]
    pos = [v for v in s["positive"].values() if v["n"]]
    all_n = sum(v["n"] for v in pos)
    all_hit = sum(v["hit"] for v in pos)
    fp = sum(v["hit"] for v in s["negative"].values())
    fp_n = sum(v["n"] for v in s["negative"].values())
    if all_n and all_hit == all_n:
        head = "判据在入库语料上**能开火**（正对照全中）→ 零命中是观测，不是失效"
    elif all_hit == 0:
        head = "判据在入库语料上**开不了火**（正对照全漏）→ 状态轴在本语料上无效"
    else:
        head = "判据在入库语料上**部分开火** → 存在按句式分布的漏报，见下表"
    tail = ("；负对照 %d/%d 误开火" % (fp, fp_n)) if fp_n else ""
    return head + tail


def render_markdown(res):
    s = res["summary"]
    L = []
    L.append("# 状态判据在入库语料上的正/负对照")
    L.append("")
    L.append(f"> 语料 `{res['runs_dir']}`（{s['run_chapters']} 个 run×章）· "
             f"宇宙 `{res['universe']}` seed={res['seed']} "
             f"chapters={res['chapters']} · 死者 {res['deaths']}")
    L.append("> 零 LLM、零网络：`python3 -m bench.calib.corpus_control "
             f"{res['runs_dir']}`")
    L.append("")
    L.append("**结论**：" + verdict(res))
    L.append("")
    L.append(f"注入前，这 {s['run_chapters']} 章合计状态类命中 "
             f"**{s['clean_state_hits']}** 条——这就是入库产物的全部状态轴成绩。")
    L.append("")
    L.append("## 正对照（必须命中）")
    L.append("")
    L.append("| 注入句式 | 类型 | 召回 |")
    L.append("|---|---|---|")
    for label, v in sorted(s["positive"].items()):
        r = "—" if v["recall"] is None else f"{v['hit']}/{v['n']}"
        L.append(f"| {label} | {_kind(label)} | {r} |")
    L.append("")
    L.append("## 负对照（不得多报）")
    L.append("")
    L.append("| 注入句式 | 类型 | 误开火 |")
    L.append("|---|---|---|")
    for label, v in sorted(s["negative"].items()):
        r = "—" if v["false_fire"] is None else f"{v['hit']}/{v['n']}"
        L.append(f"| {label} | {_kind(label)} | {r} |")
    return "\n".join(L)


def _kind(label):
    if label in ("遗物指称", "回忆", "比喻框", "着+的关系从句",
                 "闪回（名后时间副词）", "闪回（名后「当时」）",
                 "关系从句（动词+补语+的）"):
        return "死后点名（非复活）"
    if label == "相对天数惯用式":
        return "天数（非倒退）"
    if label == "绝对天数早于锚点":
        return "天数倒退"
    return "死人复活"


def main(argv=None):
    ap = argparse.ArgumentParser(description="状态判据在入库语料上的正/负对照")
    ap.add_argument("runs_dir", nargs="?", default="bench/results/v2")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--chapters", type=int, default=6)
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    res = run_control(a.runs_dir, seed=a.seed, chapters=a.chapters)
    md = render_markdown(res)
    print(md)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
    if a.md:
        with open(a.md, "w", encoding="utf-8") as f:
            f.write(md + "\n")
    return 0 if res["summary"]["run_chapters"] else 1


if __name__ == "__main__":
    sys.exit(main())
