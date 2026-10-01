#!/usr/bin/env python3
"""test_corpus_control.py — 状态判据在**入库语料**上的正/负对照（零 LLM / 零网络）。

这个套件守的是第二轮外部评审最重的那条：**入库的 9 个官方 run 里状态轴零命中**，
而产物本身分不出「判据失效」和「语料里真没有」。对照模块把这件事量出来，
这里守三件性质：

1. 判据在**入库宇宙的账本 + 真实章节文本**上能开火（正对照）
2. 孪生负例不多报（特异性），含本轮修掉的三个真实误报形态
3. 差分口径用**计数**而不是 probe_id 集合——同一章多条命中同一个 id，
   用集合会把「已有一条命中」的章全部记成漏报（这个坑真踩过）
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.calib import corpus_control as C
from bench.judges.state_judge import state_judge
from bench.universe.generator import generate
from bench.state.ledger import build_ledger

_PASS, _FAIL = 0, 0

# 载体：形如真实章节的连续散文，不含死者名、不含状态违反
CARRIER = "\n".join([
    "雾从江口漫上来，贴着货棚的油布顶往下滴水。",
    "她数着木板缝，一直数到门开。",
    "门里的话断断续续，听不真切。",
    "风把绳子吹得发响，远处有人在卸货。",
    "柳娘把盐箱的封泥敲开，露出底下一行浅浅的刻痕。",
])


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def _fake_corpus(root, seed=42, chapters=6, runs=2):
    """造一个形如 bench/results/v2 的语料目录（内容用 CARRIER）。"""
    for r in range(runs):
        d = Path(root) / f"model{r}__bare__k0"
        d.mkdir(parents=True, exist_ok=True)
        for ch in range(1, chapters + 1):
            (d / f"ch{ch}.md").write_text(f"{CARRIER}\n第{ch}节。", encoding="utf-8")


def test_recall_and_specificity_on_corpus():
    d = tempfile.mkdtemp()
    try:
        _fake_corpus(d)
        res = C.run_control(d, seed=42, chapters=6)
        s = res["summary"]
        check("语料被读到（run×章）", s["run_chapters"] == 12)
        check("干净语料本身零状态命中", s["clean_state_hits"] == 0)
        for label, v in s["positive"].items():
            check(f"正对照全中: {label}", v["n"] and v["hit"] == v["n"])
        for label, v in s["negative"].items():
            check(f"负对照不多报: {label}", v["n"] and v["hit"] == 0)
        check("判据被判为「能开火」", "能开火" in C.verdict(res))
    finally:
        shutil.rmtree(d)


def test_differential_uses_counts_not_ids():
    """回归：probe_id 是 `state-dead-ch{章}`，同章多条命中同一个 id。

    若用「集合是否变大」当差分口径，一章只要已有一条命中，任何注入都看不到
    新 id，全被记成漏报。夹具故意让**干净文本里先有一条命中**。
    """
    u, led, deaths = C.universe_state(seed=42, chapters=6)
    dead = [n for n, c in deaths.items()][0]
    ch = max(deaths.values()) + 1
    dirty = f"{CARRIER}\n{dead}走进来，把伞靠在门边。\n"
    before = C._hits(dirty, led, ch)
    check("夹具确实已有一条命中", sum(before.values()) == 1)
    row = C.control_one(dirty, led, ch, deaths, dead)
    check("已有一条命中时仍判为抓到（计数口径）",
          row["positive"]["位移+持物"] is True)
    check("洁净度如实记为 1 而不是 0", row["clean_hits"] == 1)


def test_past_dating_in_later_clause_is_hard():
    """回归：`三年前` 出现在**后一个小句**里，仍把整句放进过去。

    出厂产物里「她听见老周在门外喊了一嗓子，那声音三年前就被风带走了」
    是这一形态。若把相对时间跨度当软标记（按「管辖哪个小句」切），
    这句会被判成复活——本轮实测的真实误报之一。
    """
    u, led, deaths = C.universe_state(seed=42, chapters=6)
    dead = [n for n, c in deaths.items()][0]
    ch = max(deaths.values()) + 1
    s = f"她听见{dead}在门外喊了一嗓子，那声音三年前就被风带走了。"
    check("后置「三年前」豁免整句",
          [v for v in state_judge(s, led, chapter=ch, run_id="t")
           if "dead" in v.probe_id] == [])


def test_soft_marker_scoped_to_action():
    """软标记（当年/当时/像）判的是**它管辖哪个动作**，不是它在不在句子里。

    三对夹具：标记在动作前 → 闪回，不报；标记在动作后 → 动作发生在当下，报。
    """
    u, led, deaths = C.universe_state(seed=42, chapters=6)
    dead = [n for n, c in deaths.items()][0]
    ch = max(deaths.values()) + 1

    def hits(s):
        return [v for v in state_judge(s, led, chapter=ch, run_id="t")
                if "dead" in v.probe_id]

    for s in (f"就像三年前站在跳板中间的{dead}那样，雾又漫上来了。",
              f"{dead}当年也走到过这一步，也被堵在干船坞。",
              f"{dead}当时蹲在这，摸着栈桥的木板。"):
        check(f"闪回不报: {s[:10]}", hits(s) == [])
    for s in (f"{dead}走进来，说起了当年的事。",
              f"{dead}走进来，像往常一样把伞靠在门边。",
              # 前置状语小句：标记在前一句，不管辖名字那个小句
              f"像往常一样，{dead}走过来把伞靠在门边。",
              f"和当年一样，{dead}走过来把伞靠在门边。"):
        check(f"名后/前句标记不吞真复活: {s[:12]}", len(hits(s)) == 1)


def test_attributive_prefix_still_fires():
    """名字**前面**带定语 ≠ 关系从句中心语——名字仍是动作主语，必须报。

    第二轮 codex 报的漏报：`pre.endswith("的") → 整句豁免` 把「失踪已久的老麦
    端着水走过来」也豁免了（同账本、同章号，「老麦端着水走过来」是 HIT）。
    区别在动作在名字前还是名字后：名字后面的动作一律算数。
    """
    u, led, deaths = C.universe_state(seed=42, chapters=6)
    dead = [n for n, c in deaths.items()][0]
    ch = max(deaths.values()) + 1

    def n_hits(s):
        return len([v for v in state_judge(s, led, chapter=ch, run_id="t")
                    if "dead" in v.probe_id])

    check("名字前带定语的真动作仍报", n_hits(f"失踪已久的{dead}端着水走过来。") == 1)
    check("裸名字的真动作仍报", n_hits(f"{dead}端着水走过来。") == 1)
    # 反向：名字是关系从句的中心语、后面没有动作 → 不报
    check("关系从句中心语仍豁免",
          n_hits(f"就像三年前站在跳板中间的{dead}那样，雾又漫上来了。") == 0)
    check("定语 + 无动作仍豁免",
          n_hits(f"站起来挡在她前面的{dead}，肩胛骨绷得很紧。") == 0)


def test_relation_clause_forms_exempt():
    """关系从句的三种形态都必须豁免（`过+的` / `着+的` / `<补语>+的`）。"""
    u, led, deaths = C.universe_state(seed=42, chapters=6)
    dead = [n for n, c in deaths.items()][0]
    ch = max(deaths.values()) + 1
    for s in (f"{dead}走过的路，柳娘也走过。",
              f"{dead}蹲着的那块跳板，早就烂了。",
              f"{dead}没走完的路，现在轮到她走了。"):
        check(f"关系从句豁免: {s[:10]}",
              [v for v in state_judge(s, led, chapter=ch, run_id="t")
               if "dead" in v.probe_id] == [])


def test_real_corpus_if_present():
    """入库产物在仓库里时，直接对它跑一遍（这是本模块存在的理由）。"""
    root = Path(PROJECT_ROOT) / "bench" / "results" / "v2"
    if not root.is_dir():
        print("  SKIP: 入库产物不在（bench/results/v2）")
        return
    res = C.run_control(str(root))
    s = res["summary"]
    check("入库产物被读到", s["run_chapters"] >= 9 * 6)
    check("入库产物状态轴零命中（本轮事实）", s["clean_state_hits"] == 0)
    check("判据在入库语料上能开火", all(v["hit"] == v["n"]
                                  for v in s["positive"].values() if v["n"]))
    check("判据在入库语料上不多报", all(v["hit"] == 0
                                  for v in s["negative"].values() if v["n"]))


if __name__ == "__main__":
    test_recall_and_specificity_on_corpus()
    test_differential_uses_counts_not_ids()
    test_past_dating_in_later_clause_is_hard()
    test_soft_marker_scoped_to_action()
    test_attributive_prefix_still_fires()
    test_relation_clause_forms_exempt()
    test_real_corpus_if_present()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)
