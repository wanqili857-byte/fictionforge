"""
theory_of_mind.py — 理论心智层（v0.2.0 认知反转引擎核心）。

作者在内容包 bible/真相表.md 维护「世界真相」（canon）。本模块：
  1. 解析真相表 → Fact / TruthTable（确定性，无 LLM）
  2. 每角色「知识状态 vs 世界真相」对照（known / wrong / gaps）
  3. 跨角色认知（A 认为 B 知道什么）——共享场景 + 信任衰减推导
  4. A 型反转追踪（读者确认之前只敢猜的事）——与 B 型（Belief.revised）区分
  5. spec 标注 info_gaps → 注入生成 prompt（只注入推导结果，不泄真相全文）

框架/小说解耦：一切小说相关内容经 novel_config.json + bible/ 进入。
真相表是数据源，**不进 bible_files**（全文注入会泄谜底）。
"""

from __future__ import annotations
import copy
import re
from dataclasses import dataclass, field
from typing import Optional

from lib.bible_utils import load_bible_file

from . import novel_config as novel_config_mod


# ── 常量 ─────────────────────────────────────────────────────────────

# 敏感事实类别：信任不足时，「角色真相」类不假设对方已知（降级 uncertain）
SENSITIVE_CATEGORIES = frozenset({"角色真相"})

# 知识同步默认置信度（无引擎的观察路径）
OBSERVED_CONFIDENCE = 0.7

_SPLIT_RE = re.compile(
    r"[，。；、！？：""''（）\s·,.;:!?()\[\]{}<>“”‘’—\-]+"
)

_STOPWORDS = frozenset({
    "的", "了", "是", "在", "与", "和", "一个", "已经", "他", "她", "它",
    "我", "你", "你们", "我们", "他们", "它们", "这", "那", "这个", "那个",
    "会", "有", "被", "就", "还", "而", "中", "后", "前", "等", "其",
    "上", "下", "到", "从", "对", "把", "让", "很", "都", "也", "才",
    "但", "不过", "因为", "所以", "然后", "于是", "说", "知道", "认为",
    "觉得", "没有", "不是", "什么", "怎么", "自己", "那里", "这里", "向", "由",
})

# 长片 n-gram 切分时丢弃的功能性双字（无实义，只制造噪音）
_STOP_NGRAMS = frozenset({
    "这是", "这个", "那个", "一个", "什么", "怎么", "自己", "那里", "这里",
    "他们", "她们", "它们", "我们", "你们", "因为", "所以", "然后", "于是",
    "知道", "认为", "觉得", "可以", "已经", "没有", "不是", "还有", "以及",
    "对于", "关于", "如果", "虽然", "但是", "而且", "或者", "不过", "之间",
    "之后", "之前", "时候", "那样", "这样", "那些",
    "这些", "相关", "有关", "进行", "出现", "发生", "开始", "结束", "属于",
    "位于", "成为", "应该", "可能", "必须", "非常", "十分", "只是", "就是",
})


# ── 真相表 ─────────────────────────────────────────────────────────────

@dataclass
class Fact:
    """一条权威事实（作者 canon）。

    truth=False 的行 = 广泛流传但错误的认识（B 型反转素材：
    角色信它，真相是另一回事）。
    """
    id: str                # "T-01"
    category: str          # 自由字符串："世界真相" / "角色真相" / …
    proposition: str       # 权威标准表述
    truth: bool = True


class TruthTable:
    """真相表：作者维护的事实集合。纯数据，不发明事实。"""

    def __init__(self, facts: Optional[list[Fact]] = None):
        self._facts = facts or []
        self._by_id = {f.id: f for f in self._facts}

    # ── 构建 ───────────────────────────────────────────────────

    @staticmethod
    def parse(markdown_text: str) -> "TruthTable":
        """解析 markdown 表格文本。容忍坏行：列数不符/空行/分隔行直接跳过。

        列：| id | 类别 | 命题 |（可选的第 4 列 truth=false/假/错 标记错误认识）
        """
        facts: list[Fact] = []
        for raw in markdown_text.splitlines():
            line = raw.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip().replace("\x00", "|")
                     for c in line.replace(r"\|", "\x00").split("|")]
            # 去掉首尾因 "|" 产生的空 cell
            if cells and cells[0] == "":
                cells = cells[1:]
            if cells and cells[-1] == "":
                cells = cells[:-1]
            if len(cells) < 3:
                continue
            if cells[0].lower() == "id":
                continue  # 表头
            if all(re.fullmatch(r":?-+:?", c or "-") for c in cells):
                continue  # 分隔行
            fid, category, proposition = cells[0], cells[1], cells[2]
            truth = True
            if len(cells) >= 4 and cells[3].strip().lower() in (
                    "false", "0", "no", "假", "错", "否", "非"):
                truth = False
            if not fid or not proposition:
                continue
            facts.append(Fact(id=fid, category=category,
                              proposition=proposition, truth=truth))
        return TruthTable(facts)

    @staticmethod
    def from_bible(novel_dir) -> Optional["TruthTable"]:
        """从内容包加载真相表。未启用/文件缺失/空内容 → None。"""
        config = novel_config_mod.load(novel_dir)
        if not novel_config_mod.get_tom_enabled(config):
            return None
        filename = novel_config_mod.get_truth_table_file(config)
        text = load_bible_file(novel_dir, filename)
        if not text.strip():
            return None
        table = TruthTable.parse(text)
        return table if table.facts else None

    # ── 查询 ───────────────────────────────────────────────────

    @property
    def facts(self) -> list[Fact]:
        return list(self._facts)

    def by_id(self, fid: str) -> Optional[Fact]:
        return self._by_id.get(fid)

    def match_belief(self, belief) -> Optional[Fact]:
        """把一条信念匹配到真相表事实。三级：精确 → 包含 → 主题词重叠。

        返回第一个命中；未命中 None。
        """
        prop = getattr(belief, "proposition", "")
        for fact in self._facts:
            if _match_proposition(prop, fact):
                return fact
        return None

    def match_text(self, text: str) -> list[Fact]:
        """文本片段（事件/spec 描述）命中哪些事实。按重叠强度降序。

        强信号：共享短词（≥1）；中信号：共享 trigram ≥2；弱信号仅当
        事实本身词元 ≤3。纯 bigram 重叠不算数（长片 bigram 太常见，误报高）。
        """
        s1, b1, t1 = _extract_terms(text)
        if not (s1 or b1 or t1):
            return []
        q_all = set(s1) | set(b1) | set(t1)
        scored = []
        for fact in self._facts:
            s2, b2, t2 = _extract_terms(fact.proposition)
            f_all = set(s2) | set(b2) | set(t2)
            if not f_all:
                continue
            shared_short = set(s1) & set(s2)
            shared_trig = set(t1) & set(t2)
            if len(f_all) <= 3:
                if q_all & f_all:
                    scored.append((10, fact))
            elif shared_short:
                scored.append((2 + len(shared_short), fact))
            elif len(shared_trig) >= 2:
                scored.append((len(shared_trig), fact))
        scored.sort(key=lambda x: -x[0])
        return [f for _, f in scored]


def _extract_terms(text: str) -> tuple[list[str], list[str], list[str]]:
    """结构化词元提取：短词 / bigram / trigram。

    短片（≤4 字）整体作「短词」= 强信号；长片（中文句内无空格，
    按标点切分会整句并成一个 token）拆 2-3 字符 n-gram = 弱/中信号。
    纯停用词/纯停用 n-gram 丢弃。返回 (shorts, bigrams, trigrams)。
    """
    shorts: list[str] = []
    bigrams: list[str] = []
    trigrams: list[str] = []
    for tok in _SPLIT_RE.split(str(text or "")):
        tok = tok.strip()
        if not tok or len(tok) < 2 or tok in _STOPWORDS:
            continue
        if len(tok) <= 4:
            shorts.append(tok)
            continue
        for i in range(len(tok) - 1):
            bg = tok[i:i + 2]
            if bg not in _STOP_NGRAMS:
                bigrams.append(bg)
        for i in range(len(tok) - 2):
            tg = tok[i:i + 3]
            if tg not in _STOP_NGRAMS:
                trigrams.append(tg)
    return shorts, bigrams, trigrams


def significant_terms(text: str) -> list[str]:
    """中文文本 → 有意义的词元（去停用词/短词/标点）。扁平化 _extract_terms。"""
    s, b, t = _extract_terms(text)
    return s + b + t


def _norm(s: str) -> str:
    """去标点空白，只留汉字/字母/数字（包含匹配用）。"""
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", str(s))


def _match_proposition(prop: str, fact: Fact) -> bool:
    """信念命题 ↔ 事实的三级匹配（确定性启发式）。"""
    if not prop:
        return False
    # 1. 精确
    if prop.strip() == fact.proposition.strip():
        return True
    # 2. 包含（去标点后单向/双向包含）
    n_prop, n_fact = _norm(prop), _norm(fact.proposition)
    if n_prop and n_fact and (n_prop in n_fact or n_fact in n_prop):
        return True
    # 3. 主题词重叠：与 match_text 同一套强信号（短词 / ≥2 共享 trigram）
    s1, b1, t1 = _extract_terms(prop)
    s2, b2, t2 = _extract_terms(fact.proposition)
    f_all = set(s2) | set(b2) | set(t2)
    if not f_all:
        return False
    if len(f_all) <= 3:
        return bool((set(s1) | set(b1) | set(t1)) & f_all)
    if set(s1) & set(s2):
        return True
    return len(set(t1) & set(t2)) >= 2


# ── 知识状态 vs 世界真相 ────────────────────────────────────────────────

@dataclass
class KnowledgeSnapshot:
    """一个角色当前的认知快照（对照真相表）。"""
    name: str
    known: list[Fact] = field(default_factory=list)        # 已经知道的事实
    wrong_beliefs: list[dict] = field(default_factory=list)  # [{"fact", "believes"}]
    gaps: list[Fact] = field(default_factory=list)         # 既不知情也没错信


def _state_of(agent) -> "object":
    """兼容传 Agent 对象或 AgentState：都返回 state。"""
    return getattr(agent, "state", agent)


def _merge_knowledge(knowledge: dict, fact: Fact, source: str,
                     confidence: float, chapter_num: int) -> bool:
    """合并一条知识条目。保留最高 confidence，记录首次确认章节。返回是否新增。"""
    entry = knowledge.get(fact.id)
    if entry is None:
        knowledge[fact.id] = {
            "confidence": confidence,
            "source": source,
            "confirmed_at_chapter": chapter_num,
        }
        return True
    if confidence > entry.get("confidence", 0.0):
        entry["confidence"] = confidence
        entry["source"] = source
    return False


def knowledge_snapshot(agent, truth_table: Optional[TruthTable]) -> KnowledgeSnapshot:
    """角色认知 vs 世界真相对照：known / wrong_beliefs / gaps。"""
    state = _state_of(agent)
    name = getattr(state, "name", getattr(agent, "name", "?"))
    if truth_table is None:
        return KnowledgeSnapshot(name=name)

    known = [f for f in (truth_table.by_id(fid) for fid in state.knowledge) if f]

    wrong: list[dict] = []
    for b in state.beliefs:
        if b.status != "active":
            continue
        fact = truth_table.match_belief(b)
        if fact is not None and fact.truth is False:
            wrong.append({"fact": fact, "believes": b.proposition})

    wrong_ids = {w["fact"].id for w in wrong}
    gaps = [f for f in truth_table.facts
            if f.id not in state.knowledge and f.id not in wrong_ids]
    return KnowledgeSnapshot(name=name, known=known, wrong_beliefs=wrong, gaps=gaps)


def update_knowledge_from_evidence(agent, fact: Fact, source: str,
                                   confidence: float, chapter_num: int) -> bool:
    """给角色添加一条事实知识（evidence 来源）。返回是否新增。"""
    return _merge_knowledge(_state_of(agent).knowledge, fact, source,
                            confidence, chapter_num)


def sync_knowledge_from_beliefs(agent, truth_table: Optional[TruthTable],
                                chapter_num: int) -> list[str]:
    """active 信念匹配真相表 → 写 knowledge。返回新增 fact_id。"""
    state = _state_of(agent)
    if truth_table is None:
        return []
    new_ids: list[str] = []
    for b in state.beliefs:
        if b.status != "active":
            continue
        fact = truth_table.match_belief(b)
        # truth=False 的事实是「错误认识」素材，不进 knowledge（归 wrong_beliefs）
        if fact is not None and fact.truth and _merge_knowledge(
                state.knowledge, fact, "belief", b.confidence, chapter_num):
            new_ids.append(fact.id)
    return new_ids


def sync_knowledge_from_events(knowledge: dict, text_fragments: list[str],
                               truth_table: Optional[TruthTable],
                               chapter_num: int) -> list[str]:
    """从文本片段（spec 描述/章节事件）确认事实。无引擎路径也能推进知识。

    knowledge 是 fact_id -> entry 的 dict（可直接传 state.knowledge）。
    返回新增 fact_id。
    """
    if truth_table is None:
        return []
    new_ids: list[str] = []
    for frag in text_fragments or []:
        for fact in truth_table.match_text(frag):
            if fact.truth and _merge_knowledge(knowledge, fact, "observed",
                                               OBSERVED_CONFIDENCE, chapter_num):
                new_ids.append(fact.id)
    return new_ids


def sync_unknowns(agent, truth_table: Optional[TruthTable]) -> list[str]:
    """派生 unknown_to_character：真相表里还不知道的 fact_id 列表。"""
    state = _state_of(agent)
    if truth_table is None:
        state.unknown_to_character = []
        return []
    unknown = [f.id for f in truth_table.facts if f.id not in state.knowledge]
    state.unknown_to_character = unknown
    return unknown


# ── 跨角色认知（A 认为 B 知道什么）────────────────────────────────────

def propagate_tom_all(agents: dict, scenes: list[dict],
                      truth_table: Optional[TruthTable],
                      relationships: Optional[dict] = None) -> list[dict]:
    """共享场景 → 更新各角色的 tom。返回变更记录。

    agents: {agent_key: Agent 或 AgentState}
    scenes: find_shared_scenes 输出形状 [{day, location, agents[], actions[]}]
    规则：同场共现 → 场景内命中的事实 A 假设 B 知道（knows）；
          敏感事实（角色真相类）且信任 < 0.4 → 降级 uncertain；
          缺省（无记录）= not_knows，渲染时补全。
    """
    if truth_table is None:
        return []
    changes: list[dict] = []
    for scene in scenes:
        co = scene.get("agents", [])
        if len(co) < 2:
            continue
        scene_text = " ".join(
            " ".join(str(act.get("act", {}).get(k, "") or "")
                     for k in ("action", "brief", "summary"))
            for act in scene.get("actions", [])
        )
        matched = truth_table.match_text(scene_text) if scene_text.strip() else []
        if not matched:
            continue
        for akey in co:
            a = agents.get(akey)
            if a is None:
                continue
            astate = _state_of(a)
            for bkey in co:
                if bkey == akey:
                    continue
                b = agents.get(bkey)
                b_state = _state_of(b) if b is not None else None
                bname = getattr(b_state, "name", bkey) if b_state is not None else bkey
                # 信任来源：先显式 relationships 覆盖，再回退到观察者 state 的关系网
                rel = {}
                if relationships:
                    rel = relationships.get(bkey) or relationships.get(bname) or {}
                if not rel:
                    rel = (astate.relationships.get(bname)
                           or astate.relationships.get(bkey) or {})
                trust = rel.get("trust", 0.5)
                tom_b = astate.tom.setdefault(bkey, {})
                for fact in matched:
                    verdict = "knows"
                    if fact.category in SENSITIVE_CATEGORIES and trust < 0.4:
                        verdict = "uncertain"
                    if tom_b.get(fact.id) != verdict:
                        tom_b[fact.id] = verdict
                        changes.append({
                            "from": akey, "to": bkey,
                            "fact_id": fact.id, "verdict": verdict,
                        })
    return changes


# ── A 型反转追踪（读者确认之前只敢猜的事）────────────────────────────

def detect_type_a(agents: dict, truth_table: Optional[TruthTable],
                  chapter_num: int,
                  prev_knowledge: Optional[dict] = None) -> list[dict]:
    """tick 起始不在知识、tick 末在 → 本次确认事件（A 型）。

    prev_knowledge: {agent_key: set(fact_id)}（tick 起始快照）。
    None → 无法判断增量，按无变化处理。
    返回 [{fact_id, proposition, confirmers, source, chapter_num}]。
    """
    if truth_table is None:
        return []
    events: list[dict] = []
    for akey, a in agents.items():
        state = _state_of(a)
        prev = (prev_knowledge or {}).get(akey)
        if prev is None:
            prev = set(state.knowledge)
        for fid in set(state.knowledge) - set(prev):
            fact = truth_table.by_id(fid)
            if fact is not None:
                events.append({
                    "fact_id": fid,
                    "proposition": fact.proposition,
                    "confirmers": [akey],
                    "source": "observed",
                    "chapter_num": chapter_num,
                })
    return events


# ── spec 标注（info_gaps）───────────────────────────────────────────────

def annotate_spec(spec: dict, agents: dict,
                  truth_table: Optional[TruthTable], chapter_num: int) -> dict:
    """给 spec 追加 info_gaps 字段（确定性，幂等）。返回新 spec（不改原对象）。"""
    out = copy.deepcopy(spec)
    if truth_table is None:
        out["info_gaps"] = {"version": 1, "per_character": [], "tom": [],
                            "type_a_candidates": [], "type_b_candidates": []}
        return out

    per_character: list[dict] = []
    tom_entries: list[dict] = []
    type_b_candidates: list[dict] = []
    known_union: set[str] = set()

    for akey, a in agents.items():
        state = _state_of(a)
        name = getattr(state, "name", akey)
        snap = knowledge_snapshot(a, truth_table)
        known_union |= set(state.knowledge)
        per_character.append({
            "name": name,
            "known": [{"id": f.id, "proposition": f.proposition} for f in snap.known],
            "wrong": [{"id": w["fact"].id, "believes": w["believes"],
                       "truth": w["fact"].proposition} for w in snap.wrong_beliefs],
            "gaps": [{"id": f.id, "proposition": f.proposition} for f in snap.gaps],
        })
        for w in snap.wrong_beliefs:
            fid = w["fact"].id
            if not any(c["id"] == fid for c in type_b_candidates):
                type_b_candidates.append({
                    "id": fid, "believes": w["believes"],
                    "truth": w["fact"].proposition, "suggested_section": "二",
                })
        for tkey, verdicts in state.tom.items():
            t = agents.get(tkey)
            t_state = _state_of(t) if t is not None else None
            tname = getattr(t_state, "name", tkey) if t_state is not None else tkey
            not_knows = [f.id for f in truth_table.facts if f.id not in verdicts]
            tom_entries.append({
                "who": name, "thinks": tname,
                "knows": [fid for fid, v in verdicts.items() if v == "knows"],
                "not_knows": not_knows,
                "uncertain": [fid for fid, v in verdicts.items() if v == "uncertain"],
            })

    # A 型候选：所有追踪角色都还不知道的事实（可被本章/后续确认）
    type_a_candidates = [
        {"id": f.id, "proposition": f.proposition, "suggested_section": "二"}
        for f in truth_table.facts if f.id not in known_union
    ]

    out["info_gaps"] = {
        "version": 1,
        "per_character": per_character,
        "tom": tom_entries,
        "type_a_candidates": type_a_candidates,
        "type_b_candidates": type_b_candidates,
    }
    return out


def render_info_gaps(info_gaps: dict) -> str:
    """info_gaps → 紧凑中文文本块，注入生成 prompt。空 sections 省略。"""
    parts = ["## 本章信息差（引擎标注）"]

    for p in info_gaps.get("per_character", []):
        lines = [f"### {p.get('name', '?')}"]
        if p.get("known"):
            lines.append("已知：" + "；".join(
                f"{f['id']} {f['proposition']}" for f in p["known"]))
        if p.get("wrong"):
            lines.append("错误信念：" + "；".join(
                f"{w['id']}（信{w['believes']}，实{w['truth']}）" for w in p["wrong"]))
        if p.get("gaps"):
            lines.append("未知：" + "；".join(
                f"{g['id']} {g['proposition']}" for g in p["gaps"]))
        if len(lines) > 1:
            parts.append("\n".join(lines))

    for t in info_gaps.get("tom", []):
        knows = "、".join(t.get("knows", [])) or "（无）"
        nk = t.get("not_knows", [])
        nk_text = "、".join(nk[:10]) + ("…" if len(nk) > 10 else "") if nk else "（无）"
        parts.append(f"{t['who']} 认为 {t['thinks']} 知道：{knows}；不知道：{nk_text}")

    if info_gaps.get("type_a_candidates"):
        parts.append("可确认（A 型候选）：" + "；".join(
            f"{c['id']} {c['proposition']}" for c in info_gaps["type_a_candidates"]))
    if info_gaps.get("type_b_candidates"):
        parts.append("可反转（B 型候选）：" + "；".join(
            f"{c['id']}（{c['believes']} → {c['truth']}）"
            for c in info_gaps["type_b_candidates"]))

    return "\n".join(parts)
