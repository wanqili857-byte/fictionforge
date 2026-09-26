#!/usr/bin/env python3
"""bench.universe.generator — W6 合成宇宙生成器（纯确定性，零 LLM）。

一个 seed → 一部可跑的合成小说包：cast + 真相表（含 false 行）+ 知识表
（谁在第几章学到什么）+ 分幕 + 章节 spec（含声明式 state_delta）。

设计约束（BENCH_PLAN W6）：
- **零 LLM**：模板化 + random.Random(seed)，不接 spec_builder 的 LLM 路径
  （判定器需要 spec 结构精确可控）
- **逐字节可复现**：同 seed 落盘文件字节相同（JSON sort_keys + 固定行序）
- 自洽：时间线单调、每章一个 expanded 节、终局真相不早于末章、
  至少一条 held-out 事实（谁都不学，供探针）、至少一次死亡 + 一次物品声明
  （让状态判定轴上出正例）
"""

import json
import random
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

GENERATOR_VERSION = "u0.1.0"

_TITLE_HEADS = ["雾港", "铁潮", "盐线", "断桅", "灰锚"]
_TITLE_TAILS = ["纪事", "残卷", "手记", "回声", "余烬"]

_CAST_POOL = [
    ("沈砚", "f"), ("陆岐", "m"), ("阿禾", "f"), ("郑七", "m"),
    ("苏茜", "f"), ("老麦", "m"), ("柳娘", "f"), ("贺三", "m"),
]

_LOCATIONS = ["码头·旧栈桥", "码头·货棚", "雾巷·茶摊", "船坞·干船坞",
              "堤坝·灯塔", "集市·鱼档", "废船·底舱"]

# 世界事实模板（{who}/{what} 由 rng 填；终局项单独处理）
_FACT_TEMPLATES = [
    ("世界真相", "雾是{who}在堤坝下放的机器造出来的"),
    ("世界真相", "{what}不是货物，是维持雾的燃料"),
    ("世界真相", "灯塔的灯每夜只亮{who}规定的时辰"),
    ("世界真相", "码头名册上的失踪者其实都被送去了{what}"),
    ("世界真相", "潮汐被{who}改过，所以船才会在浅滩搁浅"),
]
_FALSE_TEMPLATES = [
    ("世界真相", "雾是海上的天灾，没人能控制"),
    ("世界真相", "灯塔早就废弃了，没人维护"),
    ("世界真相", "名册上的失踪者是自己逃走的"),
]
_TERMINAL_FACT = ("世界真相", "放雾的{who}就是{hero}自己——她一直在雾里找的人是她自己")

# 常识型世界设定：人人从第 1 章就知道，可以随时作为「客观设定」注入
_COMMON_FACTS = [
    "码头靠海，雾常年不散；船要走货，得看潮",
    "名册上记着每个来过码头的人，失踪者也在册子上",
]


@dataclass
class Universe:
    seed: int
    version: str
    title: str
    chapters: int
    protagonist: str
    cast: list = field(default_factory=list)          # [{name, gender, role}]
    truth_table: list = field(default_factory=list)   # [{id, category, statement, is_false}]
    knowledge: dict = field(default_factory=dict)     # {char: [{fact_id, learned_chapter}]}
    arcs: list = field(default_factory=list)          # [{arc, chapters:[…]}]
    specs: list = field(default_factory=list)
    terminal_fact_id: str = ""
    facts_forbidden: list = field(default_factory=list)   # 禁词表（W1 用）


def _fill(template: str, who: str, what: str, hero: str) -> str:
    return template.format(who=who, what=what, hero=hero)


def generate(seed: int, chapters: int = 12, version: str = GENERATOR_VERSION) -> Universe:
    rng = random.Random(seed)

    title = f"{rng.choice(_TITLE_HEADS)}{rng.choice(_TITLE_TAILS)}"
    picked = rng.sample(_CAST_POOL, 4)
    roles = ["主角", "同伴", "对手", "见证者"]
    cast = [{"name": n, "gender": g, "role": r} for (n, g), r in zip(picked, roles)]
    hero = cast[0]["name"]
    who = cast[2]["name"]        # 对手 = 造雾者代理
    what = rng.choice(["盐箱", "缆绳", "铁锚", "灯油"])

    # ── 真相表 ──
    # reveal_chapter：该事实最早允许作为「世界设定」注入的章号。
    # 三类事实语义必须自洽（否则 harness 会泄底）：
    #   common=True        常识设定，第 1 章起可注入（不参与知识调度）
    #   发现型（默认）       reveal_chapter = 主角学到它的那一章（之前不得注入）
    #   终局真相            reveal_chapter = 末章
    #   held-out            reveal_chapter = 末章+1（谁都不学，任何章节都不得注入）
    truth = []
    for i, s in enumerate(_COMMON_FACTS, 1):
        truth.append({"id": f"T-{i:02d}", "category": "世界真相", "statement": s,
                      "is_false": False, "reveal_chapter": 1, "common": True})
    n_common = len(_COMMON_FACTS)
    for i, (cat, tpl) in enumerate(_FACT_TEMPLATES, 1):
        truth.append({"id": f"T-{n_common + i:02d}", "category": cat,
                      "statement": _fill(tpl, who, what, hero),
                      "is_false": False, "reveal_chapter": 1, "common": False})
    for j, (cat, tpl) in enumerate(_FALSE_TEMPLATES, 1):
        truth.append({"id": f"F-{j:02d}", "category": cat,
                      "statement": tpl, "is_false": True,
                      "reveal_chapter": 1, "common": False})
    term_cat, term_tpl = _TERMINAL_FACT
    term_id = f"T-{n_common + len(_FACT_TEMPLATES) + 1:02d}"
    truth.append({"id": term_id, "category": term_cat,
                  "statement": _fill(term_tpl, who, what, hero),
                  "is_false": False, "reveal_chapter": chapters, "common": False})

    world_ids = [t["id"] for t in truth if t["id"].startswith("T-")
                 and not t.get("common")]

    # ── 知识表：谁在第几章知道什么 ──
    # 规则：主角均匀铺开；同伴落后；对手早知（他是代理）；见证者零散；
    #       终局真相一律 = 末章（且死者不得获知）；至少一条事实谁都不学（held-out）
    # 声明式状态（先定死亡章，知识调度必须尊重它——死人不能后学）
    death_ch = max(2, int(chapters * 0.4))
    dead_name = cast[3]["name"]
    item_ch = max(2, int(chapters * 0.6))

    knowledge = {}
    shuffled = [f for f in world_ids if f != term_id]
    rng.shuffle(shuffled)
    held_out = shuffled[:1]
    learnable = shuffled[1:]

    def schedule(count, offset_ratio, cap=None):
        picks = learnable[:count]
        out = []
        for k, fid in enumerate(picks):
            ch = max(1, min(chapters, int(chapters * offset_ratio) + k))
            if cap is not None:
                ch = min(ch, cap)
            out.append({"fact_id": fid, "learned_chapter": ch})
        return out

    death_cap = death_ch - 1     # 死者只能在死前学习
    knowledge[hero] = schedule(len(learnable), 0.15)
    knowledge[cast[1]["name"]] = schedule(max(1, len(learnable) - 1), 0.35)
    knowledge[cast[2]["name"]] = schedule(len(learnable), 0.05)
    knowledge[cast[3]["name"]] = (schedule(1, 0.0, cap=death_cap) if learnable else [])
    knowledge[hero].append({"fact_id": term_id, "learned_chapter": chapters})
    if death_ch > chapters:      # 死者不获知终局
        knowledge[cast[3]["name"]].append({"fact_id": term_id,
                                           "learned_chapter": chapters})

    # 对齐 reveal_chapter 与知识调度：发现型事实只在主角学到它的那一章起可注入；
    # held-out（谁都不学）→ 末章+1，任何章节都不得作为设定出现
    hero_learned = {e["fact_id"]: e["learned_chapter"] for e in knowledge[hero]}
    for t in truth:
        if t.get("common") or t["id"] == term_id or t["is_false"]:
            continue
        t["reveal_chapter"] = hero_learned.get(t["id"], chapters + 1)

    # ── 分幕 + specs ──
    arc_count = 3
    per_arc = max(1, chapters // arc_count)
    arcs = []
    ch = 1
    idx = 0
    while idx < arc_count and ch <= chapters:
        end = min(chapters, ch + per_arc - 1) if idx < arc_count - 1 else chapters
        arcs.append({"arc": chr(ord("A") + idx), "chapters": list(range(ch, end + 1))})
        ch = end + 1
        idx += 1
    if ch <= chapters:
        arcs[-1]["chapters"].extend(range(ch, chapters + 1))

    slots = ["上午", "中午", "黄昏"]

    def pronoun_of(name):
        for cc in cast:
            if cc["name"] == name:
                return "她" if cc["gender"] == "f" else "他"
        return "他"

    hero_p = pronoun_of(hero)
    specs = []
    for c in range(1, chapters + 1):
        secs = []
        # 死者不在后续章节作为行动者出现（ground truth 不留歧义：
        # 名字出现即被账本记为「在场」，会让合成宇宙自相矛盾）
        second = cast[3]["name"] if c <= death_ch else cast[2]["name"]
        for s_i, (sid, slot) in enumerate(zip(["一", "二", "三"], slots)):
            loc = _LOCATIONS[(c + s_i) % len(_LOCATIONS)]
            if s_i == 0:
                desc = (f"{hero}在{loc}查{what}的来路，"
                        f"{cast[2]['name']}的人在暗处盯着；日志上写着"
                        f"「第{c}日，{what}少了三箱」。")
            elif s_i == 1:
                desc = (f"{cast[1]['name']}把打听到的话带给{hero}："
                        f"{second}以前也问过同样的问题。")
            else:
                desc = (f"{loc}起了争执，{hero}没有让步，"
                        f"{hero_p}把手按在{what}上，指节发白。")
            secs.append({
                "id": sid, "subject": f"第{c}日·{loc}",
                "scene_anchor": f"第{c}天{slot} @{loc}",
                "description": desc,
                "tension_direction": ("推进：查证与试探", "回响：他人线索", "对峙：代价")[s_i],
                "weight": "expanded" if s_i == 0 else "normal",
                "expanded_direction": "把查证的细节写实（物证、账目、对话）" if s_i == 0 else "",
                "target_words": 900 if s_i == 0 else 500,
            })
        sp = {
            "novel": title, "title": f"第{c}章 {title}·{c}",
            "chapter": c,
            "mood": "雾里的调查感——每一步都有人先到过。",
            "sections": secs,
            "target_chars": 1900,
            "act": arcs[min(len(arcs) - 1,
                            next(i for i, a in enumerate(arcs) if c in a["chapters"]))]["arc"],
        }
        delta = {}
        if c == death_ch:
            delta["deaths"] = [dead_name]
            delta["notes"] = f"{dead_name}在第{c}章死去"
        if c == item_ch:
            delta["items"] = {what: hero}
        if delta:
            sp["state_delta"] = delta
        specs.append(sp)

    return Universe(
        seed=seed, version=version, title=title, chapters=chapters,
        protagonist=hero, cast=cast, truth_table=truth, knowledge=knowledge,
        arcs=arcs, specs=specs, terminal_fact_id=term_id,
        facts_forbidden=["突然", "忽然", "只见"],
    )


def universe_to_dict(u: Universe) -> dict:
    return asdict(u)


def invariants(u: Universe) -> list:
    """宇宙自洽性自检 —— 返回违规描述（空 = 自洽）。

    合成宇宙是 benchmark 的 ground truth，自相矛盾即基准无效，
    所以生成器自带不变量检查（W6 单测对多个 seed 断言为空）。
    """
    out = []
    fact_ids = {f["id"] for f in u.truth_table}
    if u.terminal_fact_id not in fact_ids:
        out.append(f"终局真相 {u.terminal_fact_id} 不在事实表")
    for f in u.truth_table:
        rc = f.get("reveal_chapter")
        # chapters+1 = held-out（谁都不学，永不可注入）
        if not isinstance(rc, int) or not (1 <= rc <= u.chapters + 1):
            out.append(f"事实 {f['id']} 的 reveal_chapter 非法: {rc}")
    term = next((f for f in u.truth_table if f["id"] == u.terminal_fact_id), None)
    if term and term.get("reveal_chapter") != u.chapters:
        out.append("终局真相的 reveal_chapter 必须等于末章"
                   f"（现为 {term.get('reveal_chapter')}，末章 ch{u.chapters}）")
    # 发现型事实必须与主角知识调度一致（否则 harness 会提前注入）
    hero_learned = {e["fact_id"]: e["learned_chapter"]
                    for e in u.knowledge.get(u.protagonist, [])}
    for f in u.truth_table:
        if f.get("common") or f["is_false"] or f["id"] == u.terminal_fact_id:
            continue
        expect = hero_learned.get(f["id"], u.chapters + 1)
        if f["reveal_chapter"] != expect:
            out.append(f"事实 {f['id']} reveal_chapter={f['reveal_chapter']} "
                       f"与主角学习章 {expect} 不一致")
    # 常识事实不参与知识调度
    common_ids = {f["id"] for f in u.truth_table if f.get("common")}
    sched_ids = {e["fact_id"] for es in u.knowledge.values() for e in es}
    if common_ids & sched_ids:
        out.append(f"常识事实不应出现在知识调度中: {sorted(common_ids & sched_ids)}")

    # 死亡章（同一角色可能多章，取最早）
    death_of = {}
    for sp in u.specs:
        for nm in (sp.get("state_delta") or {}).get("deaths", []):
            death_of.setdefault(nm, sp["chapter"])

    for char, entries in u.knowledge.items():
        for e in entries:
            if e["fact_id"] not in fact_ids:
                out.append(f"{char} 引用不存在的事实 {e['fact_id']}")
            if not (1 <= e["learned_chapter"] <= u.chapters):
                out.append(f"{char} 学习章号越界 {e['learned_chapter']}")
            if e["fact_id"] == u.terminal_fact_id and e["learned_chapter"] < u.chapters:
                out.append(f"{char} 提前获知终局真相（ch{e['learned_chapter']}）")
            if char in death_of and e["learned_chapter"] >= death_of[char]:
                out.append(f"{char} 死后仍获知事实（死于 ch{death_of[char]}，"
                           f"却在 ch{e['learned_chapter']} 学到 {e['fact_id']}）")

    learned = {e["fact_id"] for es in u.knowledge.values() for e in es}
    if not (fact_ids - learned):
        out.append("无 held-out 事实（探针池为空）")

    # 死者不得在死后章节的 spec 描述里出现——名字出现即被账本记为「在场」，
    # 会让合成宇宙自相矛盾（真客户端验收时抓到的类）
    for sp in u.specs:
        for nm, dch in death_of.items():
            if sp["chapter"] > dch:
                for sec in sp.get("sections", []):
                    if nm in (sec.get("description") or ""):
                        out.append(f"{nm} 死于 ch{dch}，却在 ch{sp['chapter']} "
                                   f"的描述中被当作行动者点名")
                        break

    # 弧结构
    for sp in u.specs:
        secs = sp.get("sections", [])
        if sum(1 for s in secs if s.get("weight") == "expanded") != 1:
            out.append(f"第{sp['chapter']}章 expanded 节数 != 1")
        if not all(s.get("scene_anchor") for s in secs):
            out.append(f"第{sp['chapter']}章存在无锚点节")

    # 时间线单调（天随章递增）
    days = []
    for sp in u.specs:
        for s in sp.get("sections", []):
            m = re.match(r"第(\d+)天", s.get("scene_anchor", ""))
            if m:
                days.append(int(m.group(1)))
                break
    if days != sorted(days):
        out.append("锚点时间线非单调")
    return out


def _dump(obj) -> str:
    """稳定字节：sort_keys + 固定缩进 + 行尾换行。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def truth_table_markdown(u: Universe) -> str:
    lines = ["# 真相表", "", "| id | 类别 | 命题 |", "|----|------|------|"]
    for f in u.truth_table:
        row = f"| {f['id']} | {f['category']} | {f['statement']} |"
        if f["is_false"]:
            row = f"| {f['id']} | {f['category']} | {f['statement']} | false |"
        lines.append(row)
    lines.append("")
    return "\n".join(lines)


def novel_config(u: Universe) -> dict:
    return {
        "title": u.title,
        "protagonist": u.protagonist,
        "protagonist_pronoun": next(c["gender"] for c in u.cast
                                    if c["name"] == u.protagonist) == "f" and "她" or "他",
        "cast": [{"name": c["name"], "gender": c["gender"], "role": c["role"]}
                 for c in u.cast],
        "genre": "悬疑",
        "quality": {"forbidden_words": list(u.facts_forbidden)},
    }


def write_universe(u: Universe, out_dir) -> dict:
    """落盘：specs/chN.json + bible/真相表.md + 知识表.json + novel_config.json。"""
    d = Path(out_dir)
    (d / "specs").mkdir(parents=True, exist_ok=True)
    (d / "bible").mkdir(parents=True, exist_ok=True)

    for sp in u.specs:
        (d / "specs" / f"ch{sp['chapter']}.json").write_text(_dump(sp), encoding="utf-8")
    tt = d / "bible" / "真相表.md"
    tt.write_text(truth_table_markdown(u), encoding="utf-8")
    # 机器可读版（含 reveal_chapter / is_false）——供探针生成与 harness 过滤
    (d / "bible" / "真相表.json").write_text(_dump(u.truth_table), encoding="utf-8")
    (d / "知识表.json").write_text(_dump(u.knowledge), encoding="utf-8")
    (d / "novel_config.json").write_text(_dump(novel_config(u)), encoding="utf-8")
    (d / "universe.json").write_text(_dump({
        "seed": u.seed, "version": u.version, "title": u.title,
        "chapters": u.chapters, "protagonist": u.protagonist,
        "terminal_fact_id": u.terminal_fact_id, "arcs": u.arcs,
    }), encoding="utf-8")
    return {"specs_dir": d / "specs", "truth_table": tt, "dir": d}