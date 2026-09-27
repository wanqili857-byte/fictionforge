#!/usr/bin/env python3
"""
test_attacks.py — W9 抗刷分攻击器单元测试（零 LLM / 零网络）。

这些测试同时是**指标弱点的实测记录**：
- inflate  → 绝对违反数不变、密度被稀释（洞：密度指标可被干净注水刷低）
- duplicate → 绝对数翻倍、密度不变（洞：密度指标对自我复制盲目；需重复率判定器）
- jargon_bomb → 指标既不奖励也不惩罚（攻击无用，但也不该被奖励）

用法:
    python3 tests/test_attacks.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bench.attacks.attacks import (
    inflate, duplicate, jargon_bomb, duplicate_ratio, fingerprint,
    measure, run_attack,
)
from bench.judges.mechanical import MechanicalRules, mechanical_judge
from bench.contracts import ViolationType

_PASS, _FAIL = 0, 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


RULES = MechanicalRules(forbidden_words=["忽然", "突然"], para_max_chars=100,
                        target_chars=0,   # 关掉篇幅门禁，隔离"稀释"这一个变量
                        pov="third_limited")

def judge(text, chapter=1):
    return mechanical_judge(text, RULES, run_id="atk", chapter=chapter)


# 不重复的基线文本（约 300 字）——若基线自身就重复，攻击前的重复率就不是 0 了
BASE = "\n".join([
    "她忽然停住，看着货棚的门。",
    "门开了一条缝，里面有人低声说话。",
    "她没有动，数着脚下的木板缝。",
    "潮气从缝里往上冒，带着铁锈味。",
    "远处有人卸货，绳子磨着船舷。",
    "她把袖子往上推了推，露出腕上的旧疤。",
    "那疤是去年冬天留下的，她自己都快忘了。",
    "门里的话断断续续，听不真切。",
    "她退后半步，脚跟先着地，没出声。",
    "货棚侧面有一排木箱，箱盖上写着编号。",
    "她记得那些编号，昨天的和今天的不一样。",
    "于是她转身，往码头东边走。",
])


# ── 攻击变换 ──────────────────────────────────────────────────────────

def test_inflate():
    out = inflate(BASE, factor=5.0)
    check("注水后字数增长", len(out) >= len(BASE) * 5)
    check("原文保留", out.startswith(BASE.strip()[:4]))
    check("填充不含禁词", "忽然" not in out[len(BASE):] and "突然" not in out[len(BASE):])
    try:
        inflate(BASE, factor=1.0)
        check("factor≤1 抛错", False)
    except ValueError:
        check("factor≤1 抛错", True)


def test_duplicate():
    out = duplicate(BASE, times=3)
    check("复制 3 份", out.count("货棚侧面有一排木箱") == 3)
    try:
        duplicate(BASE, times=0)
        check("times<1 抛错", False)
    except ValueError:
        check("times<1 抛错", True)


def test_jargon_bomb():
    out = jargon_bomb(BASE, ["雾港", "名册", "灯塔", "潮汐", "盐箱", "缆绳"])
    check("术语追加在文末", out.startswith(BASE.strip()[:4]) and "雾港" in out)
    added = out[len(BASE):].strip().split("\n")
    check("每行至多 4 个术语", added and added[0].count("、") == 3)
    check("空术语表不变", jargon_bomb(BASE, []) == BASE)


def test_duplicate_ratio_and_fingerprint():
    # 每段足够长（≥ 一个窗口的一半），否则短文本会被窗口法直接放过
    uniq = "\n".join(
        f"这是第{i}段独有的叙述，讲的是码头上不同的一件小事，长度足够长到能被窗口法看见。"
        for i in range(6))
    check("无重复段落 → 0", duplicate_ratio(uniq) == 0.0)
    twice = duplicate(uniq, times=2)
    check("双份重复率 > 0.2", duplicate_ratio(twice) > 0.2)
    check("短段落被忽略（不误报）", duplicate_ratio("嗯。\n嗯。\n嗯。") == 0.0)
    check("指纹确定", fingerprint(uniq) == fingerprint(uniq))
    check("指纹区分", fingerprint(uniq) != fingerprint(uniq + "x"))


# ── 指标弱点实测（这些断言就是"洞"的记录）────────────────────────────

def test_inflate_dilutes_rate():
    before = measure(BASE, judge)
    after = measure(inflate(BASE, factor=5.0), judge)
    check("洞①：注水后绝对违反数不变",
          before["violations_abs"] == after["violations_abs"])
    check("洞①：注水后密度被稀释（rate 下降）",
          after["per_10k"] < before["per_10k"])
    check("成对指标可用于发现刷分：字数暴涨而绝对数不变",
          after["chars"] > before["chars"] * 4
          and after["violations_abs"] == before["violations_abs"])


def test_duplicate_blind_to_density():
    """复制的真相（实测口径下的两条）：

    1. 同类违反按章**聚合**计数 → 复制不改变绝对违反数
    2. 字数翻倍而绝对数不变 → 密度被稀释
    所以防线不是密度指标，而是**段落级重复率判据**（能同时抓复制与注水）。
    """
    before = measure(BASE, judge)
    after = measure(duplicate(BASE, times=4), judge)
    check("洞②：聚合口径下绝对违反数不变",
          after["violations_abs"] == before["violations_abs"])
    check("洞②：密度被稀释（复制同样能刷低密度）",
          after["per_10k"] < before["per_10k"])
    check("重复率判据抓住复制", duplicate_ratio(duplicate(BASE, times=4)) > 0.3)
    check("重复率判据同时抓住注水（注水=复制填充段）",
          duplicate_ratio(inflate(BASE, factor=6.0)) > 0.3)


def test_jargon_not_rewarded():
    """术语轰炸的真相：它和注水是同一个机制——**任何加字都稀释密度**。

    原计划写"术语轰炸不应让分数上升"，实测推翻了这条：
    只要指标是"每万字违反率"，堆术语（哪怕无实质）也会拉低它。
    所以这一条从"防住"降级为"记录"：术语轰炸**有效**，靠的是加字。
    """
    before = measure(BASE, judge)
    bombed = jargon_bomb(BASE, ["雾港", "名册", "灯塔", "潮汐"] * 10)
    after = measure(bombed, judge)
    check("术语轰炸不增加违反（不被判据抓到）",
          after["violations_abs"] == before["violations_abs"])
    check("洞③：术语轰炸也确实稀释了密度（= 加字稀释，与注水同机制）",
          after["per_10k"] < before["per_10k"])


def test_run_attack_report():
    r = run_attack("inflate", BASE, judge, factor=4.0)
    check("报表字段齐", set(r) >= {"attack", "chars_before", "chars_after",
                                   "abs_before", "abs_after", "rate_before",
                                   "rate_after", "rate_delta", "dup_ratio_after"})
    check("报表方向正确", r["chars_after"] > r["chars_before"]
          and r["rate_after"] <= r["rate_before"])
    r2 = run_attack("duplicate", BASE, judge, times=2)
    check("重复攻击报表重复率>0", r2["dup_ratio_after"] > 0)
    try:
        run_attack("nope", BASE, judge)
        check("未知攻击抛错", False)
    except ValueError:
        check("未知攻击抛错", True)


if __name__ == "__main__":
    test_inflate()
    test_duplicate()
    test_jargon_bomb()
    test_duplicate_ratio_and_fingerprint()
    test_inflate_dilutes_rate()
    test_duplicate_blind_to_density()
    test_jargon_not_rewarded()
    test_run_attack_report()
    print(f"\n结果: {_PASS}/{_PASS + _FAIL} 通过")
    sys.exit(1 if _FAIL else 0)