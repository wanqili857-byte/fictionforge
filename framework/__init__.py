"""
framework — 通用 AI 小说叙事框架（与具体小说解耦）。

设计原则：框架在根，小说是内容包。
  - framework/ 定义管线/接口（引擎编排、叙事合成、spec 设计、质量门禁）
  - novels/<novel>/ 提供 novel_config.json + bible/ + agents/ + vault/ 等
  - 框架与小说的唯一接口是 novel_config.json

导入本包时自动注册项目根到 sys.path，
确保 framework 模块可 import lib.* / server.*。
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
for _p in [_PROJECT_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .agent_base import Memory, Belief, AgentState, Agent
from .api import call_llm, call_structured
from .percept_filter import PerceptionFilter
from .env_state import EnvState
from .agent_lite import LiteAgent
from .world_sim import WorldSimulator
from .narrator import Narrator
from .vault_sync import VaultSync
from .tick_runner import TickRunner
from .novel_config import load as load_novel_config
