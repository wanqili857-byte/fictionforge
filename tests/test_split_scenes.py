#!/usr/bin/env python3
"""
test_split_scenes.py — 场景聚类拆章 + spec_builder 场景绑定的单元测试。

覆盖（与任何具体小说无关，用内联通用 tick 数据）：
  Narrator._cluster_scenes     — (天,地点) 聚类
  Narrator._scene_weight       — 对手戏/隐藏信息/异常强度
  Narrator._distribute_scenes  — 按天切分，events 填入 scope
  Narrator._repair_strong_days — 无强场景章从后章挪强天
  SpecBuilder._group_scenes    — 对手戏标记（排除电话/语音误报）
  SpecBuilder._build_user_prompt — 场景分组渲染 + 反转落点锚

用法：python3 tests/test_split_scenes.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 配置来源：示例小说（novel_config 提供 pov_labels / cast / anomaly_words）
NOVEL_DIR = os.path.join(PROJECT_ROOT, "novels", "静默轨道")

from framework.narrator import Narrator, Event
from framework.spec_builder import SpecBuilder

# ── 通用 tick 数据（示例小说 cast：陆离 / 零号）────────────────────────
# 设计约束（由被测算法决定）：
#   day11 咖啡馆 = 两人当面交接 → 对手戏（强度 ≥3）
#   day12 档案馆 = 主角独场 + 私密轨迹 + 异常词 → 强场景
#   day14 家     = 主角独处，无异常 → 弱场景
#   day15 家     = 语音通话（非当面）→ 不标对手戏
#   4 天拆 3 章 → 切分 [11] / [12] / [14,15]，day12 落第6章（scopes[1]）
GENERIC_TICK = {
    "arc_id": "示例弧",
    "chapters_covered": "5-7",
    "reversal_plan": {
        "type": "B",
        "description": "以为安全了——没有",
        "position": "ch7 结尾",
    },
    "new_hooks": [],
    "character_trajectories": {
        "luli": [
            {"day": 11, "time": "09:00", "location": "咖啡馆",
             "brief": "陆离在咖啡馆等零号，交接一份文件。", "pov": "luli"},
            {"day": 12, "time": "10:00", "location": "档案馆",
             "brief": "陆离在档案馆核对档案，发现时间戳异常。", "pov": "luli"},
            {"day": 14, "time": "20:00", "location": "家",
             "brief": "陆离独自在家整理笔记。", "pov": "luli"},
        ],
        "linghao": [
            {"day": 11, "time": "09:10", "location": "咖啡馆",
             "brief": "零号把文件递给陆离，低声说了句什么。", "pov": "linghao"},
            {"day": 15, "time": "21:00", "location": "家",
             "brief": "零号打语音电话给陆离，提醒他注意数据。", "pov": "linghao"},
        ],
        "luli_private": [
            {"day": 12, "time": "10:30", "location": "档案馆",
             "brief": "陆离私下比对签名，发现档案被人改写过。",
             "pov": "luli_private", "visible_to_protagonist": False},
        ],
    },
    "suggested_chapter_split": [
        {"chapter_num": 5, "events": [
            {"day": 11, "time": "09:00", "location": "咖啡馆",
             "brief": "陆离在咖啡馆等零号，交接一份文件。", "pov": "luli"},
            {"day": 11, "time": "09:10", "location": "咖啡馆",
             "brief": "零号把文件递给陆离，低声说了句什么。", "pov": "linghao"},
        ]},
        {"chapter_num": 6, "events": [
            {"day": 12, "time": "10:00", "location": "档案馆",
             "brief": "陆离在档案馆核对档案，发现时间戳异常。", "pov": "luli"},
            {"day": 12, "time": "10:30", "location": "档案馆",
             "brief": "陆离私下比对签名，发现档案被人改写过。",
             "pov": "luli_private", "visible_to_protagonist": False},
        ]},
        {"chapter_num": 7, "events": [
            {"day": 14, "time": "20:00", "location": "家",
             "brief": "陆离独自在家整理笔记。", "pov": "luli"},
            {"day": 15, "time": "21:00", "location": "家",
             "brief": "零号打语音电话给陆离，提醒他注意数据。", "pov": "linghao"},
        ]},
    ],
}

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def load_tray():
    """从通用 tick 的 trajectories 重建 Event 轨迹（无 LLM）。"""
    tray = {}
    for pov, evs in GENERIC_TICK["character_trajectories"].items():
        tray[pov] = [
            Event(day=e["day"], time=e.get("time", ""),
                  location=e.get("location", ""),
                  brief=e.get("brief", ""), pov=e.get("pov", pov))
            for e in evs
        ]
    return tray


def test_cluster():
    print("\n── Narrator._cluster_scenes ──")
    tray = load_tray()
    scenes = Narrator(novel_dir=NOVEL_DIR)._cluster_scenes(tray)

    # (天,地点) 唯一键 → 场景数 = 不同组合数
    keys = {(e.day, e.location) for evs in tray.values() for e in evs}
    check("场景数 = (天,地点) 组合数", len(scenes) == len(keys),
          f"{len(scenes)} vs {len(keys)}")
    check("场景按天序排列", scenes == sorted(scenes, key=lambda s: (s["day"], s["first_time"])))

    # day11 咖啡馆 = 当面交接 → 对手戏，强度 ≥3
    cafe = next(s for s in scenes if s["location"] == "咖啡馆")
    check("咖啡馆场景标对手戏", cafe["interactive"] is True, str(cafe))
    check("咖啡馆强度 ≥3", cafe["weight"] >= 3, f"weight={cafe['weight']}")

    # day14 家（独处）不标对手戏
    home = next(s for s in scenes if s["day"] == 14 and s["location"] == "家")
    check("day14 家场景不标对手戏（独处）", home["interactive"] is False, str(home))

    # day15 家（语音通话）不标对手戏——电话不是当面交流
    phone_home = next(s for s in scenes if s["day"] == 15 and s["location"] == "家")
    check("day15 家不标对手戏（语音不是当面）", phone_home["interactive"] is False,
          str(phone_home))


def test_distribute():
    print("\n── Narrator._distribute_scenes（按天切分） ──")
    tray = load_tray()
    scopes = Narrator(novel_dir=NOVEL_DIR)._suggest_chapter_split(tray, {"chapters": [5, 6, 7]})

    check("3 章", len(scopes) == 3, f"{len(scopes)}")
    for sc in scopes:
        check(f"第{sc.chapter_num}章 events 已填", len(sc.events) > 0, f"{len(sc.events)}")

    # 天是原子单位：任何一章内部的事件天数不能是断开的
    for sc in scopes:
        days = sorted({e.day for e in sc.events})
        check(f"第{sc.chapter_num}章天连续", days == list(range(days[0], days[-1] + 1)), str(days))

    # 档案馆（day12）应整章落在 ch6，不被拆开
    ch6 = scopes[1]
    arch_days = {e.day for e in ch6.events}
    check("ch6 含 day12（档案馆）", 12 in arch_days, str(arch_days))


def test_repair_strong():
    print("\n── Narrator._repair_strong_days ──")
    n = Narrator()
    # 5 天 3 章。ch1=[11] 无强天；ch2=[12,13] 两个强天 → 挪 12 给 ch1。
    day_list = [11, 12, 13, 14, 15]
    day_strong = {11: 0, 12: 1, 13: 1, 14: 0, 15: 0}
    day_cuts = [1, 3, 5]  # ch1=[11], ch2=[12,13], ch3=[14,15]
    fixed = n._repair_strong_days(day_list, day_strong, day_cuts)
    first_seg = day_list[0:fixed[0]]
    second_seg = day_list[fixed[0]:fixed[1]]
    check("第1章被挪入强天", any(day_strong[d] >= 1 for d in first_seg),
          f"cuts={fixed} first_seg={first_seg}")
    check("第2章仍有强天", any(day_strong[d] >= 1 for d in second_seg), f"second={second_seg}")
    # 第1章至少要有一条天（挪后非空）
    check("第1章非空", len(first_seg) >= 1, f"first_seg={first_seg}")


def test_group_scenes():
    print("\n── SpecBuilder._group_scenes（对手戏标记） ──")
    b = SpecBuilder(novel_dir=NOVEL_DIR)
    ch5 = b._get_chapter_events(GENERIC_TICK, 5)
    scenes5 = b._group_scenes(ch5)

    cafe = next(s for s in scenes5 if s["location"] == "咖啡馆")
    check("咖啡馆标对手戏（当面交接）", cafe["interactive"] is True, str(cafe))

    ch7 = b._get_chapter_events(GENERIC_TICK, 7)
    scenes7 = b._group_scenes(ch7)
    phone_home = next(s for s in scenes7 if s["day"] == 15 and s["location"] == "家")
    check("day15 家不标对手戏（语音通话）", phone_home["interactive"] is False, str(phone_home))
    alone_home = next(s for s in scenes7 if s["day"] == 14 and s["location"] == "家")
    check("day14 家不标对手戏（独处）", alone_home["interactive"] is False, str(alone_home))


def test_user_prompt():
    print("\n── SpecBuilder._build_user_prompt ──")
    b = SpecBuilder(novel_dir=NOVEL_DIR)

    p5 = b._build_user_prompt(GENERIC_TICK, 5)
    check("ch5 渲染场景分组", "## 本章场景（1 场）" in p5)
    check("ch5 标注咖啡馆对手戏", "（对手戏）" in p5 and "咖啡馆" in p5)
    check("ch5 反转不在本章锚", "反转不在本章" in p5)

    p7 = b._build_user_prompt(GENERIC_TICK, 7)
    check("ch7 反转在本章锚", "反转在本章" in p7)

    # scene_anchor 字段已在 schema 里
    import framework.spec_builder as sb
    items = sb.SPEC_SCHEMA["properties"]["sections"]["items"]["properties"]
    check("schema 含 scene_anchor", "scene_anchor" in items)
    check("scene_anchor 在 required", "scene_anchor" in sb.SPEC_SCHEMA["properties"]["sections"]["items"]["required"])


if __name__ == "__main__":
    test_cluster()
    test_distribute()
    test_repair_strong()
    test_group_scenes()
    test_user_prompt()
    print(f"\n{'=' * 40}\nPASS {PASS} / {PASS + FAIL}")
    sys.exit(0 if FAIL == 0 else 1)
