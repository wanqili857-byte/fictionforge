"""
vault_reader.py — Read structured data from Obsidian vault markdown files.

Vault files use YAML frontmatter (--- delimited) for structured metadata.
This module parses them without requiring the PyYAML dependency,
using a simple regex-based frontmatter extractor.

Usage:
    from vault_reader import VaultReader
    vr = VaultReader("novels/示例/vault")
    ch = vr.load_chapter_state(4)
    ctx = vr.build_context_for_model(5)
"""

import os
import re
from pathlib import Path
from typing import Any, Optional


# ── frontmatter parser (no PyYAML dependency) ──────────────────────────
#
# Handles nested sequences of mappings (e.g. state_history with list items
# that each contain multiple key:value pairs at deeper indent).
# Uses indentation tracking rather than regex matching on key-value patterns.

_FM_RE = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)


def _indent(line: str) -> int:
    """Return indent level (number of leading spaces) of a line."""
    return len(line) - len(line.lstrip())


def _parse_value(raw: str):
    """Parse a YAML scalar value."""
    raw = raw.strip()
    if not raw or raw.lower() == 'null':
        return None
    if raw.lower() == 'true':
        return True
    if raw.lower() == 'false':
        return False
    # Quoted strings
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        return raw[1:-1]
    # Numbers
    try:
        return int(raw) if raw.isdigit() or (raw.startswith('-') and raw[1:].isdigit()) else float(raw)
    except ValueError:
        pass
    return raw


def _parse_block(lines: list[str], start: int, parent_indent: int = 0):
    """Parse a YAML block (mapping or sequence) at a given indent level.

    Returns (parsed_value, next_line_index).
    At the top level this returns a dict (mapping).
    If the block starts with a list item, it returns a list.
    """
    if start >= len(lines):
        return None, start

    # Skip blank lines at start
    i = start
    while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith('#')):
        i += 1
    if i >= len(lines):
        return None, i

    first_line = lines[i]
    first_stripped = first_line.strip()
    first_indent = _indent(first_line)

    # Detect if we're in a sequence (starts with "- ")
    if first_stripped.startswith('- '):
        return _parse_sequence_block(lines, i, first_indent)
    else:
        return _parse_mapping_block(lines, i, first_indent)


def _parse_mapping_block(lines: list[str], start: int, block_indent: int):
    """Parse a block of key: value pairs at the same indent level.
    Returns (dict, next_line_index)."""
    result = {}
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith('#'):
            i += 1
            continue

        # If indent decreased below block_indent, we're done
        current_indent = _indent(line)
        if current_indent < block_indent and stripped:
            break
        # If this is a list item at or below block_indent, we're done with mapping
        if stripped.startswith('- ') and current_indent <= block_indent:
            break

        # Must start with a key name
        colon_pos = stripped.find(':')
        if colon_pos <= 0:
            i += 1
            continue

        key = stripped[:colon_pos].strip()
        val_raw = stripped[colon_pos + 1:].strip()
        i += 1

        if not val_raw:
            # Value may be nested block on following lines
            if i < len(lines):
                next_indent = _indent(lines[i])
                if next_indent > current_indent:
                    nested, i = _parse_block(lines, i, next_indent)
                    result[key] = nested
                else:
                    result[key] = None
            else:
                result[key] = None
        elif val_raw.startswith('[') and val_raw.endswith(']'):
            # Inline list
            items = val_raw[1:-1].split(',')
            result[key] = [_parse_value(x) for x in items]
        elif val_raw == '|':
            # Block scalar — collect following indented lines as a single string
            lines_collected = []
            base_indent = current_indent + 2
            while i < len(lines) and _indent(lines[i]) >= base_indent:
                lines_collected.append(lines[i].strip())
                i += 1
            result[key] = '\n'.join(lines_collected)
        else:
            result[key] = _parse_value(val_raw)

    return result, i


def _parse_sequence_block(lines: list[str], start: int, block_indent: int):
    """Parse a block of list items at the same indent level.
    Handles items that are simple values and items that are nested mappings.
    Returns (list, next_line_index)."""
    result = []
    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith('#'):
            i += 1
            continue

        current_indent = _indent(line)
        if current_indent < block_indent and stripped:
            break

        # Check for list item
        if not stripped.startswith('- '):
            break

        item_content = stripped[2:].strip()
        i += 1

        # Check if this list item has nested content on following lines
        next_has_nested = False
        if i < len(lines):
            next_indent = _indent(lines[i])
            if next_indent > current_indent:
                next_has_nested = True

        if next_has_nested:
            # Parse the nested mapping
            nested, i = _parse_mapping_block(lines, i, current_indent + 2)
            result.append(nested)
        else:
            # Simple list item (could be key: value on same line, or just a scalar)
            # 如果以引号开头，一定是纯字符串——不是 key: value
            is_quoted = item_content.startswith('"') or item_content.startswith("'")
            colon_pos = item_content.find(':')
            if colon_pos > 0 and not is_quoted:
                key = item_content[:colon_pos].strip()
                val_raw = item_content[colon_pos + 1:].strip()
                if val_raw:
                    result.append({key: _parse_value(val_raw)})
                else:
                    # key with no value — might have value on next indented line
                    if i < len(lines) and _indent(lines[i]) > current_indent:
                        nested, i = _parse_block(lines, i, current_indent + 2)
                        result.append({key: nested})
                    else:
                        result.append({key: None})
            else:
                result.append(_parse_value(item_content))

    return result, i


def parse_frontmatter(text: str) -> dict:
    """Extract and parse YAML frontmatter from markdown text.
    Returns empty dict if no valid frontmatter."""
    m = _FM_RE.search(text)
    if not m:
        return {}

    fm_text = m.group(1)
    lines = fm_text.split('\n')
    result, _ = _parse_block(lines, 0, 0)
    return result if isinstance(result, dict) else {}


def parse_frontmatter_file(filepath: str) -> dict:
    """Read a file and parse its frontmatter."""
    if not os.path.exists(filepath):
        return {}
    with open(filepath, 'r', encoding='utf-8') as f:
        return parse_frontmatter(f.read())


# ── VaultReader class ──────────────────────────────────────────────

class VaultReader:
    """Reads structured narrative data from an Obsidian vault directory."""

    def __init__(self, vault_dir: str):
        self.vault_dir = Path(vault_dir)
        assert self.vault_dir.is_dir(), f"Vault directory not found: {vault_dir}"
        self._chapter_cache = {}

    # ── Chapter state ───────────────────────────────────────────────

    def load_chapter_state(self, chapter_num: int) -> dict:
        """Load frontmatter from vault/章节状态/第N章.md"""
        if chapter_num in self._chapter_cache:
            return self._chapter_cache[chapter_num]
        path = self.vault_dir / "章节状态" / f"第{chapter_num}章.md"
        data = parse_frontmatter_file(str(path))
        if data:
            self._chapter_cache[chapter_num] = data
        return data

    def load_all_chapter_states(self) -> dict[int, dict]:
        """Load all chapter state files, return dict of {ch_num: data}."""
        states = {}
        ch_dir = self.vault_dir / "章节状态"
        if not ch_dir.is_dir():
            return states
        for f in sorted(ch_dir.glob("第*章.md")):
            m = re.search(r'第(\d+)章', f.name)
            if m:
                ch = int(m.group(1))
                states[ch] = self.load_chapter_state(ch)
        return states

    # ── Characters ──────────────────────────────────────────────────

    def load_character(self, name: str) -> dict:
        """Load character frontmatter from vault/人物/{name}.md"""
        path = self.vault_dir / "人物" / f"{name}.md"
        return parse_frontmatter_file(str(path))

    def load_all_characters(self) -> dict[str, dict]:
        """Load all character files from vault/人物/."""
        chars = {}
        char_dir = self.vault_dir / "人物"
        if not char_dir.is_dir():
            return chars
        for f in sorted(char_dir.glob("*.md")):
            name = f.stem  # filename without .md
            data = parse_frontmatter_file(str(f))
            if data:
                chars[name] = data
        return chars

    # ── Timeline ────────────────────────────────────────────────────

    def load_timeline(self) -> list[dict]:
        """Parse vault/时间线.md into structured event list.
        Returns list of {day, time, event, source_chapter}."""
        path = self.vault_dir / "时间线.md"
        if not path.exists():
            return []

        events = []
        with open(str(path), 'r', encoding='utf-8') as f:
            text = f.read()

        current_day = None
        # Pattern: | time | event | source |
        table_pattern = re.compile(r'^\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|')
        day_header = re.compile(r'^##\s*第(\d+)天')

        for line in text.split('\n'):
            dm = day_header.match(line)
            if dm:
                current_day = int(dm.group(1))
                continue
            tm = table_pattern.match(line)
            if tm and current_day is not None:
                time_str = tm.group(1).strip()
                event_str = tm.group(2).strip()
                source_str = tm.group(3).strip()
                if time_str and event_str and time_str not in ('时间', '---'):
                    events.append({
                        'day': current_day,
                        'time': time_str,
                        'event': event_str,
                        'source': source_str,
                    })
        return events

    # ── Hooks ───────────────────────────────────────────────────────

    def load_open_hooks(self) -> list[dict]:
        """Load unresolved hooks from vault/伏笔 ledger.md."""
        path = self.vault_dir / "伏笔 ledger.md"
        if not path.exists():
            return []

        hooks = []
        with open(str(path), 'r', encoding='utf-8') as f:
            text = f.read()

        # Parse the table sections
        in_hook_section = False
        hook_pattern = re.compile(
            r'^\|\s*(\S+)\s*\|\s*(.*?)\s*\|\s*(\S+)\s*\|\s*(\S*)\s*\|\s*(\S.*?)\s*\|'
        )

        for line in text.split('\n'):
            stripped = line.strip()
            if stripped.startswith('| ID |'):
                continue  # header row
            if stripped.startswith('|----'):
                continue  # separator row
            if stripped.startswith('## 开放'):
                in_hook_section = True
                continue
            if stripped.startswith('## ') and not stripped.startswith('## 开放'):
                in_hook_section = False
                continue
            if in_hook_section:
                hm = hook_pattern.match(stripped)
                if hm:
                    hooks.append({
                        'id': hm.group(1).strip(),
                        'description': hm.group(2).strip(),
                        'created_at': hm.group(3).strip(),
                        'last_touched': hm.group(4).strip() or hm.group(3).strip(),
                        'type': hm.group(5).strip(),
                    })
        return hooks

    # ── World & constraints ─────────────────────────────────────────

    def load_worldbuilding(self) -> dict:
        """Load structured worldbuilding from vault/世界观.md"""
        path = self.vault_dir / "世界观.md"
        return parse_frontmatter_file(str(path))

    def load_writing_rules(self) -> dict:
        """Load writing rules from vault/写作约束.md"""
        path = self.vault_dir / "写作约束.md"
        return parse_frontmatter_file(str(path))

    # ── Context assembly ────────────────────────────────────────────

    def build_context_for_model(self, chapter_num: int) -> str:
        """Aggregate all vault data into a context block for the model.
        This replaces gen.py's load_chapter_context().
        Returns a formatted string ready for system prompt injection."""
        parts = []

        # 1. Previous chapter state
        prev_ch = self.load_chapter_state(chapter_num - 1) if chapter_num > 1 else {}
        if prev_ch:
            parts.append("## 上文提要（来自 vault）")
            if prev_ch.get('key_events'):
                parts.append("### 上一章关键事件")
                for ev in prev_ch['key_events']:
                    parts.append(f"- {ev}")
            if prev_ch.get('protagonist_state'):
                ls = prev_ch['protagonist_state']
                parts.append(f"### 主角状态")
                if isinstance(ls, dict):
                    for k, v in ls.items():
                        if isinstance(v, list):
                            parts.append(f"- {k}: {'; '.join(v)}")
                        else:
                            parts.append(f"- {k}: {v}")

        # 2. Open hooks
        hooks = self.load_open_hooks()
        if hooks:
            active_hooks = [h for h in hooks if h.get('status', 'open') != 'resolved']
            if active_hooks:
                parts.append("### 未回收伏笔")
                for h in active_hooks[:8]:  # cap at 8 to avoid context overflow
                    parts.append(f"- {h['description']}")

        # 3. Character states
        chars = self.load_all_characters()
        for name, data in chars.items():
            if data.get('state_history'):
                latest = data['state_history'][-1]
                if isinstance(latest, dict):
                    parts.append(f"### {name} 当前状态")
                    parts.append(f"- 状态: {latest.get('summary', latest.get('status', ''))}")
                    parts.append(f"- 位置: {data.get('current_location', '未知')}")
                    if data.get('goals'):
                        gl = data['goals']
                        if isinstance(gl, dict) and 'immediate' in gl:
                            parts.append(f"- 目标: {gl['immediate']}")

        # 4. Timeline context for this chapter range
        now_ch = self.load_chapter_state(chapter_num) or {}
        if now_ch.get('time_span'):
            ts = now_ch['time_span']
            if isinstance(ts, dict):
                parts.append(f"### 时间范围: {ts.get('start', '?')} → {ts.get('end', '?')}")
                if ts.get('absolute_day'):
                    parts.append(f"（故事第 {ts['absolute_day']} 天）")

        # 5. World state
        world_state = now_ch.get('world_state', {})
        if isinstance(world_state, dict) and world_state.get('stage'):
            parts.append(f"### 世界阶段: {world_state.get('stage', '?')}")
            public_events = [e for e in (world_state.get('public_events') or []) if e]
            if public_events:
                parts.append(f"公众事件: {'; '.join(public_events)}")

        return '\n'.join(parts) if len(parts) > 1 else ""

    # ── Continuity check ────────────────────────────────────────────

    def check_continuity(self, chapter_num: int, spec: dict) -> list[str]:
        """Check the spec against vault data for continuity issues.
        Returns a list of warning strings."""
        warnings = []
        prev_ch = self.load_chapter_state(chapter_num - 1) if chapter_num > 1 else {}

        if not prev_ch and chapter_num > 1:
            warnings.append(f"⚠️ 无法读取第{chapter_num-1}章状态——连续性检查跳过")
            return warnings

        # 1. Check character positions
        if prev_ch and spec:
            spec_chars = set()
            for sec in spec.get('sections', []):
                desc = sec.get('description', '')
                for char_name in ['主角', '亲属', '配角', '来电者', '配角乙']:
                    if char_name in desc:
                        spec_chars.add(char_name)

        # 2. Check time consistency
        now_ch = self.load_chapter_state(chapter_num) or {}
        now_ts = now_ch.get('time_span', {})
        prev_ts = prev_ch.get('time_span', {})
        if isinstance(now_ts, dict) and isinstance(prev_ts, dict):
            now_day = now_ts.get('absolute_day')
            prev_day = prev_ts.get('absolute_day')
            if now_day and prev_day and now_day <= prev_day:
                warnings.append(f"⚠️ 时间异常：第{chapter_num}章（第{now_day}天）不晚于第{chapter_num-1}章（第{prev_day}天）")

        # 3. TODO: check open hooks are respected in spec (too complex for simple pattern matching)

        return warnings


# ── Direct CLI usage ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    vault_dir = sys.argv[1] if len(sys.argv) > 1 else "vault"
    vr = VaultReader(vault_dir)

    print("=== 角色列表 ===")
    chars = vr.load_all_characters()
    for name in chars:
        print(f"  {name}: {chars[name].get('role', '?')}")

    print("\n=== 时间线（前10条） ===")
    for ev in vr.load_timeline()[:10]:
        print(f"  第{ev['day']}天 {ev['time']}: {ev['event']}")

    print("\n=== 开放伏笔 ===")
    for h in vr.load_open_hooks():
        print(f"  {h['id']}: {h['description'][:50]}...")

    if len(sys.argv) > 2:
        ch = int(sys.argv[2])
        print(f"\n=== 第{ch}章上下文 ===")
        print(vr.build_context_for_model(ch))
