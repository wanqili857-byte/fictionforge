"""
bible_utils.py — 共享 bible 文件读取工具。

gen.py 和 engine 都从 bible/ 目录加载世界观/人设/写作法则。
本模块提供统一的文件加载入口，确保路径解析和基础读取一致。

输出格式化由各管线自己负责（gen.py 产出结构规则块，engine 产出简练 lore）。
"""

from __future__ import annotations
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=16)
def load_bible_file(novel_dir: str | Path, filename: str) -> str:
    """读取 bible/<filename>，返回全文文本。

    文件不存在时返回空字符串。
    gen.py 和 engine 共用，确保路径解析一致。
    """
    path = Path(novel_dir) / "bible" / filename
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")
