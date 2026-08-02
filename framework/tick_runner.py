"""
tick_runner.py — 弧级模拟编排器。

1 tick = 1 故事弧，编排流程：

1. 加载 vault 起始状态
2. 初始化各层 Agent
3. 运行世界模拟器 → 世界状态
4. 感知过滤 → 各 Agent 看到不同世界
5. 各 Agent 独立决策 → 行动线
6. Narrator 合成 → TickResult
7. VaultSync 准备回写数据
"""

from __future__ import annotations
import json
import os
import re
import sys
from typing import Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.log import get_logger
log = get_logger("engine")

from .agent_lite import LiteAgent
from .world_sim import WorldSimulator
from .narrator import Narrator, TickResult
from .vault_sync import VaultSync, MemoryStore
from .env_state import EnvState
from .percept_filter import PerceptionFilter
from .agent_base import AgentState, Memory
from .scene_director import resolve_shared_scenes
from . import novel_config as novel_config_mod


class TickRunner:
    """弧级模拟编排器。"""

    def __init__(self, vault_reader=None, output_dir: str = None):
        self.vault_reader = vault_reader
        self.novel = ""
        self.novel_dir = None
        self.novel_config = {}
        if vault_reader and hasattr(vault_reader, 'vault_dir'):
            vault_dir = str(vault_reader.vault_dir)
            self.memory_store = MemoryStore(vault_dir)
            # novels/<novel>/vault → 小说目录 + 小说名
            self.novel_dir = os.path.dirname(vault_dir)
            self.novel = os.path.basename(self.novel_dir)
            self.novel_config = novel_config_mod.load(self.novel_dir)
            if self.novel_dir not in sys.path:
                sys.path.insert(0, self.novel_dir)
        else:
            self.memory_store = None
        default_out = (os.path.join(self.novel_dir, "temp")
                       if self.novel_dir else
                       os.path.join(os.path.dirname(__file__), "..", "temp"))
        self.output_dir = output_dir or default_out
        self.narrator = Narrator(vault_reader, novel_dir=self.novel_dir)
        self.vault_sync = VaultSync(vault_reader)
        self.world_sim = WorldSimulator(vault_reader)
        os.makedirs(self.output_dir, exist_ok=True)

    def run(self, arc_config: dict, write_vault: bool = False) -> TickResult:
        """执行一次完整 tick。

        write_vault=True 时自动回写 vault 文件。

        arc_config 格式：
        {
            "arc_id": "某弧",
            "chapters": [5, 6, 7],
            "time_range": {"start": "第11天", "end": "第14天+"},
            "reversal_type": "B",
            "reversal_description": "以为逃离就安全了——没有",
            "reversal_position": "ch7 结尾",
            "tension_target": 7,
            "active_regions": ["A地", "B地"],
            "tier1": ["主角", "重要配角"],
            "tier2": ["配角"],
            "world_modules": ["government", "media", "infrastructure", "public"],
            "protagonist_hint": "本弧主角的主要行动目标",
        }
        """
        log.info(f"\n=== Tick: {arc_config.get('arc_id', '?')} ===")
        log.info(f"  章节: {arc_config.get('chapters')}")

        # 1. 加载 vault 起始状态
        start_ch = min(arc_config.get("chapters", [1]))
        vault_state = self._load_vault_state(start_ch)

        # 2. 初始化 Agent
        agents = self._init_agents(arc_config, vault_state)
        agent_outputs = {}

        # 3. 环境状态初始化
        env_state = EnvState.from_vault_reader(self.vault_reader, start_ch - 1)
        # 应用 arc 的时间信息
        start_str = arc_config.get("time_start", "")
        if start_str:
            m = re.search(r'第(\d+)天', start_str)
            if m:
                target_day = int(m.group(1))
                current_day = env_state.time.absolute_day
                if target_day > current_day:
                    env_state.time.advance(24 * (target_day - current_day))

        # 4. 运行世界模拟器
        log.info("  [WorldSim] 运行世界模拟...")
        world_output = self.world_sim.run(arc_config, vault_state)
        if not world_output:
            log.warning("  [WorldSim] 世界模拟器无输出，使用降级数据")
            world_output = self._fallback_world_output(arc_config)

        # 5. 构建世界输出结构
        world_structured = self._build_world_structure(world_output, env_state)

        # 6. 各 Agent 决策（并行）
        log.info("  [Agents] 并行决策...")
        agent_outputs = self._run_agents_parallel(agents, world_structured, arc_config)

        # 6.5 两阶段对手戏：共享场景（同天同地）交互解析
        forbidden = (self.novel_config.get("quality", {})
                     .get("forbidden_words", None))
        agent_outputs = resolve_shared_scenes(
            agent_outputs, world_structured, forbidden)

        # 7. 环境状态更新
        self._update_env_from_world(env_state, world_output)

        # 8. Narrator 合成
        log.info("  [Narrator] 合成中...")
        tick_result = self.narrator.synthesize(
            arc_config, agent_outputs, world_output, env_state.to_dict()
        )
        tick_result.novel = self.novel

        # 8.25 弧末反思：Tier1 把弧内记忆压缩成高阶洞察（先于持久化）
        n_refl = self._run_reflections(agents, arc_config)
        if n_refl:
            log.info(f"  [Reflect] {n_refl} 条反思洞察已存入记忆")

        # 8.5 持久化 agent memories
        if self.memory_store:
            for name, agent in agents.items():
                n_mems = len(agent.state.memories)
                if n_mems:
                    self.memory_store.save(name, agent.state.memories,
                                            arc_config.get("arc_id", ""))
                    log.info(f"  [MemoryStore] {name}: {n_mems} 条记忆已持久化")

        # 9. VaultSync 准备回写
        log.info("  [VaultSync] 准备 vault 更新...")
        if write_vault and self.vault_reader:
            vault_dir = self.vault_reader.vault_dir if hasattr(self.vault_reader, 'vault_dir') else None
            if vault_dir:
                self.vault_sync.write_to_vault(
                    self.narrator.to_json(tick_result), arc_config, vault_dir
                )

        # 10. 输出
        self._save_tick_result(tick_result, arc_config)

        return tick_result

    def _run_agents_parallel(self, agents: dict, world_structured: dict,
                              arc_config: dict) -> dict[str, dict]:
        """并行执行各 Agent 的 perceive + decide。"""
        outputs = {}

        def _run_one(name, agent):
            perceived = agent.perceive(world_structured)
            result = agent.decide(perceived, arc_config)
            if not result:
                log.warning(f"  [{name}Agent] 决策无输出")
                result = {"actions": [], "belief_updates": []}
            return name, result

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_run_one, name, agent): name
                for name, agent in agents.items()
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    n, result = fut.result()
                    outputs[n] = result
                except Exception as e:
                    log.warning(f"  [{name}Agent] 决策异常: {e}")
                    outputs[name] = {"actions": [], "belief_updates": []}

        return outputs

    # ── 内部方法 ──────────────────────────────────────────────────────

    def _run_reflections(self, agents: dict, arc_config: dict) -> int:
        """弧末反思：仅 Tier1 角色（Tier2 无持久记忆，跳过）。"""
        tier1_keys = {cfg["key"]
                      for cfg in novel_config_mod.tier_cast(self.novel_config, 1)}
        total = 0
        for key, agent in agents.items():
            if key not in tier1_keys:
                continue
            reflect = getattr(agent, "reflect", None)
            if not callable(reflect):
                continue
            try:
                added = reflect(arc_config=arc_config)
                total += len(added)
            except Exception as e:
                log.warning(f"  [{key}Agent] 反思失败: {e}")
        return total

    def _load_vault_state(self, chapter_num: int) -> dict:
        """从 vault 加载起始状态。"""
        if not self.vault_reader:
            return {}

        state = {"chapter_states": {}, "characters": {}}

        # 加载前 N 章状态
        for ch in range(max(1, chapter_num - 2), chapter_num):
            ch_data = self.vault_reader.load_chapter_state(ch)
            if ch_data:
                state["chapter_states"][ch] = ch_data

        # 加载角色
        if hasattr(self.vault_reader, 'load_all_characters'):
            state["characters"] = self.vault_reader.load_all_characters()

        # 取最新一章作为参考
        latest = state["chapter_states"].get(chapter_num - 1, {})
        if not latest:
            latest = state["chapter_states"].get(chapter_num, {})

        state["world_state"] = latest.get("world_state", {})
        for key, state_key in novel_config_mod.state_keys(self.novel_config).items():
            state[state_key] = latest.get(state_key, {})
        state["key_events"] = latest.get("key_events", [])

        return state

    def _init_agents(self, arc_config: dict,
                     vault_state: dict) -> dict[str, object]:
        """按 novel_config cast 初始化所有 Agent。

        Tier1 用 from_vault_state(vault_state, vault_reader, memory_store)，
        Tier2 直接实例化 (name, profile, vault_reader)。
        """
        agents = {}
        if not self.novel_config:
            log.warning("  [Agents] 无 novel_config，不初始化任何 agent")
            return agents

        # 禁词从 config 读——LiteAgent system prompt 注入用
        forbidden = (self.novel_config.get("quality", {})
                     .get("forbidden_words", None))

        for cfg in novel_config_mod.tier_cast(self.novel_config, 1):
            cls = self._import_agent(cfg)
            agents[cfg["key"]] = cls.from_vault_state(
                vault_state, self.vault_reader, self.memory_store)

        for cfg in novel_config_mod.tier_cast(self.novel_config, 2):
            profile = self._get_character_profile(cfg["name"], vault_state)
            cls = self._import_agent(cfg)
            agents[cfg["key"]] = cls(cfg["name"], profile, self.vault_reader,
                                     forbidden_words=forbidden)

        return agents

    @staticmethod
    def _import_agent(cfg: dict):
        """按 cast 配置导入 agent 类：impl 模块 + class 名。"""
        import importlib
        mod = importlib.import_module(cfg["impl"])
        return getattr(mod, cfg["class"])

    def _get_character_profile(self, name: str,
                                vault_state: dict) -> Optional[dict]:
        """从 vault 获取角色 profile。"""
        chars = vault_state.get("characters", {})
        if name in chars:
            profile = dict(chars[name])
            ch_states = vault_state.get("chapter_states", {})
            for ch_num, ch_data in ch_states.items():
                if isinstance(ch_data, dict):
                    chars_present = ch_data.get("characters_present", [])
                    if name in chars_present:
                        profile["current_location"] = ch_data.get("location",
                                                                    profile.get("current_location", "?"))
                        break
            return profile

        return {
            "name": name,
            "role": f"配角（{name}）",
            "current_location": vault_state.get("chapter_states", {})
                                .get(max(vault_state.get("chapter_states", {}) or {1}), {})
                                .get("location", "?"),
            "key_function": "",
        }

    def _build_world_structure(self, world_output: dict,
                                env_state: EnvState) -> dict:
        """将世界模拟输出转为 public/traces/hidden 三层结构。"""
        public = {
            "weather": env_state.weather.condition if hasattr(env_state, 'weather') else "晴",
            "time": env_state.time.fmt() if hasattr(env_state, 'time') else "?",
            "government_stance": world_output.get("government", {}).get(
                "public_stance", ""),
            "media_reports": world_output.get("media", {}).get("reported", []),
            "public_behavior": world_output.get("public_sentiment", {}).get(
                "collective_behavior", []),
            "infrastructure": world_output.get("infrastructure", {}),
        }

        traces = world_output.get("public_traces", [])
        hidden = world_output.get("hidden_truths", [])

        return {"public": public, "traces": traces, "hidden": hidden}

    def _update_env_from_world(self, env_state: EnvState,
                                world_output: dict):
        """根据世界模拟输出更新环境状态。"""
        infra = world_output.get("infrastructure", {})
        for region, status in infra.get("power", {}).items():
            loc = env_state.get_location(region)
            loc.power = status
            if status == "down":
                loc.mutation_level = max(loc.mutation_level, 3)

        ps = world_output.get("public_sentiment", {})
        env_state.aggregated_panic = min(10, ps.get("panic_level", 0))

        gov = world_output.get("government", {})
        if gov.get("public_stance"):
            env_state.resources.notes.append(
                f"政府表态：{gov['public_stance']}")

    def _fallback_world_output(self, arc_config: dict) -> dict:
        """世界模拟器无输出时的降级数据。"""
        return {
            "government": {
                "public_stance": "暂无官方回应",
                "actions_taken": ["监测中"],
                "internal_knowledge": "未知",
                "next_expected_move": "等待",
            },
            "media": {
                "reported": [],
                "suppressed": [],
                "narrative_framing": "正常报道",
            },
            "public_sentiment": {
                "panic_level": 3,
                "rumors": ["有事情在发生"],
                "collective_behavior": ["观望"],
                "trust_in_government": 5,
            },
            "infrastructure": {
                "power": {},
                "communications": {},
                "transport": {},
            },
            "religion_marginal": {
                "interpretations": [],
                "activity_level": 0,
            },
            "public_traces": [],
            "hidden_truths": [],
        }

    def _save_tick_result(self, tick_result: TickResult, arc_config: dict):
        """保存 tick 结果到 JSON 和 markdown。"""
        data = self.narrator.to_json(tick_result)
        arc_id = arc_config.get("arc_id", "unnamed")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # JSON
        path = os.path.join(self.output_dir, f"tick_{arc_id}_{timestamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info(f"  [输出] JSON: {path}")

        latest_path = os.path.join(self.output_dir, f"tick_{arc_id}_latest.json")
        with open(latest_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # Markdown
        md = self.narrator.to_markdown(tick_result, arc_config)
        md_path = os.path.join(self.output_dir, f"tick_{arc_id}_{timestamp}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md + "\n")
        log.info(f"  [输出] Markdown: {md_path}")

        latest_md = os.path.join(self.output_dir, f"tick_{arc_id}_latest.md")
        with open(latest_md, "w", encoding="utf-8") as f:
            f.write(md + "\n")
