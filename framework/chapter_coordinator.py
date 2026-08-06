"""
chapter_coordinator.py — 顶层协调器（v0.2.0）。

每章选管线路径：
  - gen：手写 spec → ToM 标注（info_gaps）→ 推进知识 → run_generation
    （= 今日 gen.py 流程 + 理论心智层标注）
  - engine：engine_runner 跑 tick → JSON 落盘，不生成正文
  - hybrid：engine_runner → 机械 spec → ToM 标注 → run_generation

配置分层（novel_config.json + arc + CLI，见 novel_config.get_pipeline_mode）：
  cli_mode > arc_config.pipeline_mode > chapter_overrides > default_mode > "gen"

全部依赖可注入 → 无 LLM 无 I/O 可单测：
  engine_runner    缺省 = deterministic_tick（无 LLM 假 tick）
  spec_builder_fn  缺省 = build_spec_mechanical（机械，无 LLM）
  pipeline_runner   缺省 = gen.py run_generation（惰性 import，避免 framework→scripts 反依赖）

理论心智层：gen 模式用 spec 场景文本推进主角知识（确定性关键词匹配）；
真相表缺失/禁用时静默降级，行为与 v0.1.x 一致。
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import novel_config as novel_config_mod
from .theory_of_mind import TruthTable, annotate_spec, sync_knowledge_from_events
from .agent_base import AgentState


@dataclass
class CoordResult:
    """一次章节协调的产出。"""
    mode: str
    chapter_num: int
    tick_path: Optional[str] = None
    spec_path: Optional[str] = None
    output_path: Optional[str] = None
    type_a_events: list = field(default_factory=list)
    info_gaps: dict = field(default_factory=dict)
    text: str = ""


def deterministic_tick(chapter_num: int, arc_config: dict = None,
                       novel_dir: str = None) -> dict:
    """无 LLM 假 tick：shape 与 gen.py load_tick_result 一致。

    engine/hybrid 模式在无真实引擎时也可跑可测。
    """
    arc_config = arc_config or {}
    return {
        "arc_id": arc_config.get("arc_id", "deterministic"),
        "novel": os.path.basename(str(novel_dir)) if novel_dir else "",
        "chapters_covered": str(arc_config.get("chapters", [chapter_num])),
        "character_trajectories": {},
        "state_changes": {},
        "new_hooks": [],
        "suggested_chapter_split": [{
            "chapter_num": chapter_num, "title_hint": "", "sections": 3,
            "tension_direction": "", "reversal_at_section": 0, "events": [],
        }],
        "reversal_plan": {
            "type": arc_config.get("reversal_type", "A"),
            "description": arc_config.get("reversal_description", ""),
            "position": arc_config.get("reversal_position", ""),
        },
        "type_a_events": [],
    }


def _default_pipeline_runner(spec: dict, novel_dir: str, force: bool = True,
                             spec_path: str = None, output_path: str = None) -> str:
    """缺省生成入口：惰性 import gen.py（避免 framework→scripts 反依赖）。"""
    from scripts.gen import run_generation
    return run_generation(spec, novel_dir=novel_dir, force=force,
                          spec_path=spec_path, output_path=output_path)


class ChapterCoordinator:
    """顶层协调器：解析管线模式并分发到 gen/engine/hybrid。"""

    def __init__(self, novel_dir: str, *,
                 engine_runner=None, spec_builder_fn=None,
                 pipeline_runner=None, memory_store=None, output_dir: str = None):
        self.novel_dir = str(novel_dir)
        self.config = novel_config_mod.load(self.novel_dir)
        # 依赖注入（全有缺省 → 可单测）
        self.engine_runner = engine_runner
        self.spec_builder_fn = spec_builder_fn
        self.pipeline_runner = pipeline_runner or _default_pipeline_runner
        self.memory_store = memory_store
        self.output_dir = output_dir or os.path.join(self.novel_dir, "temp")
        self._last_tick_path = None

    # ── 主入口 ─────────────────────────────────────────────────────

    def run(self, chapter_num: int, spec_path: str = None,
            arc_config: dict = None, mode: str = None,
            force: bool = False) -> CoordResult:
        """协调一章。mode 缺省走配置分层（cli > arc > override > default > gen）。"""
        self.chapter_num = chapter_num
        self.arc_config = arc_config or {}
        self.spec_path = spec_path
        self.force = force
        self.mode = self._resolve_mode(mode)

        truth_table, agents = self._tom_context()

        tick = None
        spec = None
        if self.mode in ("engine", "hybrid"):
            tick = self._run_engine()

        if self.mode in ("gen", "hybrid"):
            if self.mode == "gen":
                if not spec_path:
                    raise ValueError("gen 模式需要 spec_path（手写 spec）")
                spec = self._load_spec(spec_path)
            else:
                spec = self._build_spec(tick)
            if truth_table is not None and agents:
                spec = annotate_spec(spec, agents, truth_table, chapter_num)
                if self.mode == "gen":
                    # gen 无引擎：用 spec 场景文本推进知识并落盘（下一章可读）
                    self._advance_knowledge(spec, agents, truth_table)
                    self._persist_agents(agents)
            spec_out = self._write_spec(spec)
            text = self._generate(spec)
        else:
            spec_out, text = None, None

        return CoordResult(
            mode=self.mode, chapter_num=chapter_num,
            tick_path=self._last_tick_path,
            spec_path=spec_out,
            output_path=(str(Path(self.novel_dir) / "chapters" / f"第{chapter_num}章.md")
                         if spec is not None else None),
            type_a_events=(tick or {}).get("type_a_events", []),
            info_gaps=(spec or {}).get("info_gaps", {}),
            text=text,
        )

    # ── 分发 ───────────────────────────────────────────────────────

    def _resolve_mode(self, cli_mode: Optional[str]) -> str:
        """管线模式：cli > arc > chapter_overrides > default > gen。"""
        return novel_config_mod.get_pipeline_mode(
            self.config, self.chapter_num, self.arc_config, cli_mode)

    def _run_engine(self) -> dict:
        """跑引擎 tick。engine_runner(chapter_num, arc_config) → tick dict。"""
        fn = self.engine_runner or deterministic_tick
        tick = fn(self.chapter_num, self.arc_config) or {}
        # tick JSON 落盘（engine 模式产出物）
        arc_id = self.arc_config.get("arc_id", "unnamed")
        out = Path(self.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"tick_{arc_id}_latest.json"
        path.write_text(json.dumps(tick, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        self._last_tick_path = str(path)
        return tick

    def _build_spec(self, tick: dict) -> dict:
        """engine tick → 章节 spec。缺省机械构建（无 LLM）。"""
        fn = self.spec_builder_fn or _mechanical_default
        novel = os.path.basename(self.novel_dir)
        return fn(tick, self.chapter_num, novel)

    def _load_spec(self, spec_path: str) -> dict:
        with open(spec_path, encoding="utf-8") as f:
            return json.load(f)

    def _generate(self, spec: dict) -> str:
        """调用生成管线。pipeline_runner(spec, novel_dir, force, spec_path, output_path)。"""
        out = Path(self.novel_dir) / "chapters" / f"第{self.chapter_num}章.md"
        return self.pipeline_runner(spec, novel_dir=self.novel_dir,
                                    force=self.force, spec_path=self.spec_path,
                                    output_path=str(out))

    # ── 理论心智层上下文 ───────────────────────────────────────────

    def _tom_context(self):
        """真相表 + 各 cast 角色的认知状态（knowledge/tom/beliefs）。"""
        truth_table = None
        if self.novel_dir:
            truth_table = TruthTable.from_bible(self.novel_dir)
        agents: dict[str, AgentState] = {}
        for cfg in novel_config_mod.get_cast(self.config):
            key = cfg.get("key")
            if not key:
                continue
            name = cfg.get("name", key)
            saved = self._load_state(key)
            st = AgentState(name=name)
            st.knowledge = dict(saved.get("knowledge", {}))
            st.tom = dict(saved.get("tom", {}))
            st.beliefs = list(saved.get("beliefs", []))
            agents[key] = st
        return truth_table, agents

    def _load_state(self, key: str) -> dict:
        if self.memory_store is None:
            from .vault_sync import MemoryStore
            self.memory_store = MemoryStore(str(Path(self.novel_dir) / "vault"))
        return self.memory_store.load_agent_state(key)

    def _advance_knowledge(self, spec: dict, agents: dict, truth_table: TruthTable):
        """gen 模式：spec 场景文本（确定性匹配）推进各角色知识。"""
        fragments = []
        for sec in spec.get("sections", []):
            for k in ("description", "tension_direction", "expanded_direction"):
                if sec.get(k):
                    fragments.append(str(sec[k]))
        if not fragments:
            return
        for a in agents.values():
            sync_knowledge_from_events(a.knowledge, fragments,
                                       truth_table, self.chapter_num)

    def _persist_agents(self, agents: dict):
        """gen 模式：推进后的知识落盘（下一章可读）。"""
        if self.memory_store is None:
            from .vault_sync import MemoryStore
            self.memory_store = MemoryStore(str(Path(self.novel_dir) / "vault"))
        for key, a in agents.items():
            self.memory_store.save(key, [], beliefs=a.beliefs,
                                   knowledge=a.knowledge, tom=a.tom)

    def _write_spec(self, spec: dict) -> str:
        """标注后的 spec 落盘（供审阅/复现；不覆盖手写原文件）。"""
        out = Path(self.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"ch{self.chapter_num}_spec.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return str(path)


def _mechanical_default(tick: dict, chapter_num: int, novel: str) -> dict:
    """机械 spec 兜底（惰性 import，避免模块顶层依赖 spec_builder）。"""
    from .spec_builder import build_spec_mechanical
    return build_spec_mechanical(tick, chapter_num, novel)
