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


@lru_cache(maxsize=32)
def distill_world_lore(text: str, max_lines: int = 15) -> str:
    """通用世界观蒸馏——不依赖任何小说的概念词。

    跳过 YAML frontmatter、markdown 标题、表格行、引用块、代码块，
    保留有意义的内容行（去 bullet 装饰），上限 max_lines。
    结果为空表示文档里没有可提取内容。
    """
    lines = text.split("\n")

    # 跳过 YAML frontmatter（首行为 --- 时，跳到闭合 --- 之后）
    start = 0
    if lines and lines[0].strip() == "---":
        idx = 1
        while idx < len(lines):
            if lines[idx].strip() == "---":
                start = idx + 1
                break
            idx += 1

    out: list[str] = []
    in_code = False
    for raw in lines[start:]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or line.startswith(("#", "|", ">")):
            continue
        if line.startswith("- ") or line.startswith("* "):
            line = line[2:].strip()
        if len(line) >= 4:
            out.append(line)
        if len(out) >= max_lines:
            break

    return "\n".join(out)
