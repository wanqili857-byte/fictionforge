"""
arc_loader.py — 从 markdown frontmatter 加载弧配置。

弧配置文件放在 novels/<novel>/arcs/*.md，用 YAML frontmatter 定义参数。
Obsidian 可直接编辑。

frontmatter 解析复用 vault_reader.py 的 parse_frontmatter()。

格式规则（避免嵌套，列表用 [a, b] 语法）：
  ---
  arc_id: 某弧
  chapters: [5, 6, 7]
  time_start: 第11天
  time_end: 第14天+
  tier1: [主角, 重要配角]
  waypoints: [A地, B地, C地]
  reversal_type: B
  ---
"""

from pathlib import Path
from typing import Optional

# sys.path 由 engine.__init__.py 统一注册，本模块无需再操作
from lib.vault_reader import parse_frontmatter


def load_arc_config(name_or_path: str, arcs_dir: Optional[str] = None,
                    novel_dir: Optional[str] = None) -> dict:
    """从 markdown 文件加载弧配置。

    支持：
    - 绝对路径
    - 文件名（自动在 arcs/ 目录下搜索，默认 novels/<novel>/arcs/）
    - arcs/ 下的相对路径
    """
    path = Path(name_or_path)
    if path.is_file():
        return _parse_arc_file(path)

    if arcs_dir is None:
        if novel_dir:
            arcs_dir = os.path.join(novel_dir, "arcs")
        else:
            arcs_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "novels")
    arcs_path = Path(arcs_dir)

    for f in arcs_path.glob("*.md"):
        if name_or_path in (f.name, f.stem):
            return _parse_arc_file(f)

    full = arcs_path / name_or_path
    if full.is_file():
        return _parse_arc_file(full)
    elif full.with_suffix(".md").is_file():
        return _parse_arc_file(full.with_suffix(".md"))

    raise FileNotFoundError(
        f"弧配置未找到: {name_or_path}（搜索路径: {arcs_dir}）"
    )


def _parse_arc_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    config = parse_frontmatter(text)
    if not config:
        raise ValueError(f"{path} 缺少 YAML frontmatter（---）")
    if "arc_id" not in config:
        config["arc_id"] = path.stem
    return config
