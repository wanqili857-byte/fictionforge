"""
novel_config.py — 小说内容包配置加载。

novel_config.json 是框架与小说之间的唯一接口：
框架读取它来决定 cast（谁是什么层的 agent）、pov 显示名、
bible 注入文件列表、质量参数。换小说 = 换配置 + 换内容，框架代码不动。

每个小说目录 novels/<novel>/ 下有一个 novel_config.json。
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional


def load(novel_dir: str) -> dict:
    """加载 novels/<novel>/novel_config.json。缺失返回空 dict（降级默认）。"""
    path = Path(novel_dir) / "novel_config.json"
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def novel_dir_from_vault(vault_reader) -> Optional[str]:
    """从 vault_reader 推导小说目录（novels/<novel>/）。"""
    if vault_reader is None:
        return None
    vault_dir = getattr(vault_reader, "vault_dir", None)
    if not vault_dir:
        return None
    return os.path.dirname(str(vault_dir))


def get_cast(config: dict) -> list[dict]:
    return config.get("cast", [])


def tier_cast(config: dict, tier: int) -> list[dict]:
    return [c for c in get_cast(config) if c.get("tier") == tier]


def get_pov_labels(config: dict) -> dict:
    return config.get("pov_labels", {})


def cast_names(config: dict) -> list[str]:
    """cast 里的角色中文名（对手戏检测用的名字集合）。"""
    return [c.get("name", "") for c in get_cast(config) if c.get("name")]


def protagonist_name(config: dict) -> str:
    """主角中文名（cast 里 key == protagonist 的角色名）。无则空。"""
    pkey = config.get("protagonist", "")
    return character_name(config, pkey)


def character_name(config: dict, key_or_name: str) -> str:
    """把角色 key（或已是名字）解析成中文名。未知则原样返回。"""
    if not key_or_name:
        return ""
    for c in get_cast(config):
        if c.get("key") == key_or_name or c.get("name") == key_or_name:
            return c.get("name", "")
    return key_or_name


def protagonist_pronoun(config: dict) -> str:
    """主角人称代词（"她"/"他"/"它"…）。无则空。
    用于 system prompt 硬约束：主角人称固定，全章不混用。"""
    return config.get("protagonist_pronoun", "") or ""


def get_bible_files(config: dict) -> list[str]:
    return config.get("bible_files", [])


def get_quality(config: dict) -> dict:
    return config.get("quality", {})


def state_keys(config: dict) -> dict:
    """tier1 角色 key → chapter state 里的状态字段名。"""
    return {
        c["key"]: c.get("state_key", f"{c.get('key', '?')}_state")
        for c in tier_cast(config, 1)
    }
