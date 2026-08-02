"""
agent_base.py — 智能体基础数据结构。

核心类型：
  Memory   — 单条记忆（感知 / 推理 / 目标 / 关系）
  Belief   — 信念系统（主观，可能错）
  AgentState — 智能体完整状态快照
  Agent    — 基类（感知 → 决策 → 记忆）
  MemoryRetriever — 按重要性/时间/标签检索记忆
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .api import call_structured


# ── 弧末反思（记忆巩固）───────────────────────────────────────────────

_REFLECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reflections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "一条高阶洞察：这段经历改变了你对世界/某人的什么看法，未来如何指导你的行动。用角色口吻写，第一人称，1-2 句。",
                    },
                    "importance": {"type": "integer", "minimum": 5, "maximum": 10},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["content"],
            },
        },
    },
    "required": ["reflections"],
}

_REFLECTION_SYSTEM = """你是小说角色 {name} 的「经历反思」环节。

回顾你在这个故事弧里经历的一切，把零散记忆压缩成 2-3 条高阶洞察：
- 这段经历让你对世界/对某人改变了什么看法？
- 你现在更相信什么、更怀疑什么？
- 这些洞察会在未来如何指导你的行动？

用你自己的口吻写，第一人称。不要复述事件，要提炼。

## 你当前的信念
{beliefs}

## 你记得的事
{memories}"""


# ── 记忆 ────────────────────────────────────────────────────────────────

@dataclass
class Memory:
    """一条记忆。"""
    id: str
    content: str
    type: str  # perception | inference | goal | relationship
    created_at_chapter: int
    importance: int  # 1-10，由 LLM 生成时标注
    tags: list[str] = field(default_factory=list)
    location: str = ""  # 记忆发生的地点，辅助检索


# ── 信念 ────────────────────────────────────────────────────────────────

@dataclass
class Belief:
    """信念：角色相信的某事（可能错）。

    status: active（当前相信）| revised（被新证据修正，不再作为当前信念）。
    修正不删除——旧信念被推翻这一事实本身是有用的叙事痕迹
    （认知反转 B 类：读者看到角色信念的断裂）。
    """
    subject: str        # 关于谁/什么
    proposition: str    # 信念内容
    confidence: float = 0.0   # 0.0-1.0
    source_memories: list[str] = field(default_factory=list)  # 支撑记忆 ID
    created_at_chapter: int = 0
    status: str = "active"        # active | revised
    revised_evidence: str = ""    # 修正它时留下的证据/新命题


# ── 智能体状态 ──────────────────────────────────────────────────────────

@dataclass
class AgentState:
    """智能体完整状态的快照。"""
    name: str
    role: str = ""
    # 记忆 / 信念
    memories: list[Memory] = field(default_factory=list)
    beliefs: list[Belief] = field(default_factory=list)
    # 目标层级
    goals: dict = field(default_factory=lambda: {
        "immediate": "",  # 当前场景目标
        "arc": "",        # 本章节弧目标
        "story": "",      # 全书目标
    })
    # 生理 / 感知
    emotional_state: str = "平静"
    mutation_phase: int = 0
    perception_stage: str = ""
    # 空间
    location: str = ""
    # 关系网（角色名 → {type, trust, tension}）
    relationships: dict = field(default_factory=dict)
    # 元信息：角色不知道的事（供合成器/Vault 使用）
    unknown_to_character: list[str] = field(default_factory=list)


# ── 记忆检索器 ──────────────────────────────────────────────────────────

class MemoryRetriever:
    """非 embedding 记忆检索。

    策略：
      1. importance >= 8 的必选
      2. 最近 N 章的全部记忆
      3. 按标签匹配的相关记忆
      结果合并去重，上限 20 条。
    """

    def __init__(self, recent_chapters: int = 2, max_memories: int = 20):
        self.recent_chapters = recent_chapters
        self.max_memories = max_memories

    def retrieve(self, memories: list[Memory],
                 current_chapter: int = 1,
                 tags: Optional[list[str]] = None) -> list[Memory]:
        """根据当前章节和上下文标签检索相关记忆。"""
        if not memories:
            return []

        scored = []

        for m in memories:
            score = 0

            # 重要性
            if m.importance >= 8:
                score += 5
            elif m.importance >= 5:
                score += 2

            # 时效性
            chapter_diff = current_chapter - m.created_at_chapter
            if chapter_diff <= self.recent_chapters:
                score += 3
            elif chapter_diff <= 5:
                score += 1

            # 标签匹配
            if tags and any(t in m.tags for t in tags):
                score += 2

            # 类型权重
            if m.type == "goal":
                score += 1  # 目标类记忆持续重要

            scored.append((score, m))

        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:self.max_memories]]

    def format_for_prompt(self, memories: list[Memory]) -> str:
        """将记忆列表格式化为 prompt 可用的文本块。"""
        if not memories:
            return "（无相关记忆）"
        lines = []
        for m in memories:
            tag_str = f"[{'/'.join(m.tags)}]" if m.tags else ""
            lines.append(
                f"- (ch{m.created_at_chapter}, 重要度{m.importance}) "
                f"{tag_str} {m.content}"
            )
        return "\n".join(lines)


# ── 智能体基类 ──────────────────────────────────────────────────────────

class Agent:
    """所有角色智能体的基类。

    子类必须实现：
      - build_system_prompt() → str
      - build_decision_prompt(perceived) → str
    """

    def __init__(self, name: str, state: AgentState,
                 vault_reader=None, retriever: Optional[MemoryRetriever] = None):
        self.name = name
        self.state = state
        self.vault_reader = vault_reader
        self.retriever = retriever or MemoryRetriever()

    def perceive(self, world_output: dict) -> dict:
        """由子类覆盖：应用感知过滤器，返回该 agent 能看到的世界。"""
        raise NotImplementedError

    def build_system_prompt(self) -> str:
        """由子类覆盖：返回该系统 prompt。"""
        raise NotImplementedError

    def build_decision_prompt(self, perceived: dict) -> str:
        """由子类覆盖：返回该次决策的 user prompt。"""
        raise NotImplementedError

    def decide(self, perceived: dict, api_call_fn=None) -> dict:
        """执行决策循环：构造 prompt → 调用 LLM → 解析结构化输出。

        api_call_fn: 外部注入的 call_structured 函数，便于测试时 mock。
        返回 LLM 输出的 dict。
        """
        raise NotImplementedError

    def get_relevant_context(self, current_chapter: int,
                             tags: Optional[list[str]] = None) -> list[Memory]:
        """获取相关记忆上下文。"""
        return self.retriever.retrieve(
            self.state.memories, current_chapter, tags
        )

    def format_context_for_prompt(self, current_chapter: int,
                                  tags: Optional[list[str]] = None) -> str:
        """格式化记忆上下文为 prompt 文本块。"""
        mems = self.get_relevant_context(current_chapter, tags)
        return self.retriever.format_for_prompt(mems)

    def apply_belief_updates(self, updates: list[dict]) -> list[dict]:
        """AGM 式信念修正：新增信念；显式 revises 时把旧信念标为 revised。

        updates: [{subject, proposition, confidence, revises?, evidence?}]
        - proposition 非空才处理。
        - revises 匹配到一条 active 信念的 proposition → 旧信念 status=revised，
          revised_evidence 记录证据（缺省用新命题）。
        - 旧信念不删除，仅标记。返回实际应用的条目。

        由子类 decide() 里调用（agent_linmo 等），框架层统一修正逻辑。
        """
        if not updates:
            return []
        applied = []
        for bu in updates:
            prop = bu.get("proposition", "")
            if not prop:
                continue
            revises = bu.get("revises", "")
            evidence = bu.get("evidence", "")
            if revises:
                for b in self.state.beliefs:
                    if b.status == "active" and b.proposition == revises:
                        b.status = "revised"
                        b.revised_evidence = evidence or prop
                        break
            self.state.beliefs.append(Belief(
                subject=bu.get("subject", "?"),
                proposition=prop,
                confidence=bu.get("confidence", 0.5),
                source_memories=[m.id for m in self.state.memories[-3:]],
            ))
            applied.append(bu)
        return applied

    def reflect(self, arc_config: Optional[dict] = None,
                api_call_fn=None) -> list[Memory]:
        """弧末反思：把弧内记忆压缩成 2-3 条高阶洞察，存为新记忆。

        对抗"静态角色"的关键机制——零散经历被提炼成可跨弧指导行为的信念。
        新记忆 type="reflection"，importance 高（MemoryRetriever 会优先检索）。
        调用失败返回空列表（不中断管线）。
        """
        api_fn = api_call_fn or call_structured
        arc_ch = min(arc_config.get("chapters", [1])) if arc_config else 1
        current_ch = getattr(self, '_current_arc_chapter', arc_ch)

        mems_text = self.format_context_for_prompt(
            current_chapter=current_ch, tags=None)
        beliefs_text = "；".join(
            f"{b.subject}: {b.proposition}（{b.confidence:.1f}）"
            for b in self.state.beliefs if b.status == "active"
        ) or "（无当前信念）"

        system = _REFLECTION_SYSTEM.format(
            name=self.name,
            beliefs=beliefs_text,
            memories=mems_text,
        )
        result = api_fn(system, "请输出反思洞察。", _REFLECTION_SCHEMA,
                        route_key="agent", temperature=0.7)
        if not result:
            return []

        added: list[Memory] = []
        for i, r in enumerate(result.get("reflections", [])):
            content = (r or {}).get("content", "")
            if not content:
                continue
            m = Memory(
                id=f"{self.name}_refl_{arc_ch}_{i}_{len(self.state.memories)}",
                content=content,
                type="reflection",
                created_at_chapter=arc_ch,
                importance=r.get("importance", 7),
                tags=list(r.get("tags", [])) + ["reflection"],
            )
            self.state.memories.append(m)
            added.append(m)
        return added
