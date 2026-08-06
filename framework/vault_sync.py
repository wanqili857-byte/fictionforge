"""
vault_sync.py — Tick 后 / 章节生成后的 vault 回写。

主要功能：
  1. tick 后：追加时间线事件、更新伏笔 ledger、生成角色 state_history 条目
  2. 章节生成后：更新章节状态文件 frontmatter
  3. MemoryStore — Agent memory 跨 tick 持久化
  4. 直接写入 vault 目录（文件 I/O）

注意：实际文件修改会覆盖 vault 中的对应文件。
写入前会备份原文件（.bak）。
"""

from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from lib.log import get_logger
log = get_logger("engine.vault_sync")


def _belief_to_dict(b) -> dict:
    """Belief dataclass → dict（MemoryStore 持久化用）。"""
    return {
        "subject": b.subject,
        "proposition": b.proposition,
        "confidence": b.confidence,
        "source_memories": list(b.source_memories),
        "created_at_chapter": b.created_at_chapter,
        "status": b.status,
        "revised_evidence": b.revised_evidence,
    }


def _belief_from_dict(d: dict):
    """dict → Belief。缺字段给默认值，容忍旧数据。"""
    from .agent_base import Belief
    return Belief(
        subject=d.get("subject", "?"),
        proposition=d.get("proposition", ""),
        confidence=d.get("confidence", 0.0),
        source_memories=list(d.get("source_memories", [])),
        created_at_chapter=d.get("created_at_chapter", 0),
        status=d.get("status", "active"),
        revised_evidence=d.get("revised_evidence", ""),
    )


class MemoryStore:
    """Agent memory 跨 tick 持久化。

    Memory 对象序列化为 JSON，存在 vault/engine_memories/ 目录。
    下次 tick 初始化时重新加载，实现跨 tick 记忆延续。
    """

    def __init__(self, vault_dir: str):
        self.storage_dir = Path(vault_dir) / "engine_memories"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save(self, agent_name: str, memories: list, arc_id: str = "",
             beliefs: list = None, knowledge: dict = None, tom: dict = None):
        """序列化 agent 全量状态到 JSON 文件。

        beliefs/knowledge/tom 是理论心智层的跨 tick 状态；
        不传则仅存 memories（向后兼容）。
        """
        path = self.storage_dir / f"{agent_name}.json"
        data = {
            "agent": agent_name,
            "arc_id": arc_id,
            "memories": [
                {
                    "id": m.id,
                    "content": m.content,
                    "type": m.type,
                    "created_at_chapter": m.created_at_chapter,
                    "importance": m.importance,
                    "tags": m.tags,
                    "location": m.location,
                }
                for m in memories
            ],
        }
        if beliefs is not None:
            data["beliefs"] = [_belief_to_dict(b) for b in beliefs]
        if knowledge is not None:
            data["knowledge"] = knowledge
        if tom is not None:
            data["tom"] = tom
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, agent_name: str) -> list:
        """反序列化 agent memories。不存在则返回空列表。"""
        return self.load_agent_state(agent_name)["memories"]

    def load_agent_state(self, agent_name: str) -> dict:
        """反序列化 agent 全量状态。不存在则返回空结构。

        返回 {"memories": [Memory], "beliefs": [Belief], "knowledge": dict, "tom": dict}
        """
        from .agent_base import Memory, Belief
        path = self.storage_dir / f"{agent_name}.json"
        if not path.exists():
            return {"memories": [], "beliefs": [], "knowledge": {}, "tom": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            memories = [
                Memory(
                    id=m["id"],
                    content=m["content"],
                    type=m["type"],
                    created_at_chapter=m["created_at_chapter"],
                    importance=m["importance"],
                    tags=m.get("tags", []),
                    location=m.get("location", ""),
                )
                for m in data.get("memories", [])
            ]
            beliefs = [
                _belief_from_dict(b) for b in data.get("beliefs", [])
            ]
            return {
                "memories": memories,
                "beliefs": beliefs,
                "knowledge": data.get("knowledge", {}),
                "tom": data.get("tom", {}),
            }
        except Exception as e:
            log.warning(f"  [MemoryStore] loading {agent_name} state failed: {e}")
            return {"memories": [], "beliefs": [], "knowledge": {}, "tom": {}}

    def clear(self, agent_name: str = None):
        """清理记忆存储。None = 全部清空。"""
        if agent_name:
            p = self.storage_dir / f"{agent_name}.json"
            if p.exists():
                p.unlink()
        else:
            import shutil
            shutil.rmtree(str(self.storage_dir))
            self.storage_dir.mkdir(parents=True, exist_ok=True)


class VaultSync:
    """Vault 同步器。组装 vault 更新数据。"""

    def __init__(self, vault_reader=None):
        self.vault_reader = vault_reader

    def build_timeline_entries(self, tick_result: dict,
                                arc_config: dict) -> list[dict]:
        """从 tick 输出构建时间线条目。"""
        entries = []

        for pov, events in tick_result.get("character_trajectories", {}).items():
            for ev in events:
                entries.append({
                    "day": ev.get("day", 0),
                    "time": ev.get("time", "?"),
                    "event": f"[{pov}] {ev.get('brief', '')}",
                    "source": f"ch{arc_config.get('chapters', ['?'])[0]}-{arc_config.get('chapters', ['?'])[-1]}",
                })

        # 按时间排序
        entries.sort(key=lambda e: (e["day"], e["time"]))
        return entries

    def build_hook_updates(self, new_hooks: list[dict],
                            existing_hooks: Optional[list[dict]] = None) -> dict:
        """构建伏笔 ledger 的更新数据。
        返回 {更新类型: [hook 条目]}
        """
        if existing_hooks is None:
            existing_hooks = []

        existing_ids = {h.get("id") for h in existing_hooks if h.get("id")}

        # 新伏笔（生成 ID）
        new_entries = []
        for i, h in enumerate(new_hooks):
            if h.get("id") and h["id"] in existing_ids:
                continue
            new_entries.append({
                "id": h.get("id") or f"hook_{len(existing_hooks) + i + 1:03d}",
                "description": h.get("description", ""),
                "created_at": f"ch{h.get('created_at_chapter', 1)}",
                "last_touched": f"ch{h.get('created_at_chapter', 1)}",
                "type": "新增",
                "status": "open",
            })

        return {"new": new_entries, "existing_open_count": len(existing_hooks)}

    def build_character_updates(self, tick_result: dict,
                                 arc_config: dict) -> dict[str, dict]:
        """构建各角色的 state_history 更新条目。"""
        start_ch = min(arc_config.get("chapters", [1]))
        end_ch = max(arc_config.get("chapters", [1]))

        updates = {}

        for char_name, changes in tick_result.get("state_changes", {}).get(
                "characters", {}).items():
            revised = changes.get("revised_beliefs", [])
            new_beliefs = changes.get("new_beliefs", [])
            if revised:
                summary = f"信念修正：{'、'.join(revised)}"
            elif new_beliefs:
                summary = new_beliefs[0]
            else:
                summary = "(状态更新)"
            entry = {
                "chapter": f"{start_ch}-{end_ch}",
                "summary": summary,
                "goal": changes.get("goal", ""),
            }
            if changes.get("perception_stage"):
                entry["perception_stage"] = changes["perception_stage"]
            updates[char_name] = entry

        return updates

    # ── 文件 I/O ───────────────────────────────────────────────────

    def write_timeline(self, entries: list[dict], vault_dir: str):
        """追加时间线条目到 vault/时间线.md。"""
        path = Path(vault_dir) / "时间线.md"

        # 按天分组
        by_day = defaultdict(list)
        for e in entries:
            by_day[e["day"]].append(e)

        # 构建新章节
        sections = ["\n---\n"]
        for day in sorted(by_day.keys()):
            sections.append(f"\n## 第{day}天\n")
            sections.append("| 时间 | 事件 | 来源 |")
            sections.append("|------|------|------|")
            for e in sorted(by_day[day], key=lambda x: x["time"]):
                sections.append(f"| {e['time']} | {e['event']} | {e['source']} |")
            sections.append("")

        new_content = "\n".join(sections)

        if path.exists():
            # 备份
            bak = path.with_suffix(".md.bak")
            if not bak.exists():
                path.rename(bak)
            existing = path.read_text(encoding="utf-8")
            # 插入在注脚之前（如果有）
            note_idx = existing.rfind("\n>")
            if note_idx > 0 and existing[note_idx:].strip().endswith("。"):
                existing = existing[:note_idx].rstrip() + new_content + "\n" + existing[note_idx:].lstrip()
            else:
                existing = existing.rstrip() + new_content + "\n"
        else:
            existing = "# 时间线\n\n> 引擎自动追加。\n" + new_content + "\n"

        path.write_text(existing, encoding="utf-8")
        count = len(entries)
        log.info(f"  [vault_sync] 时间线: 追加 {count} 条 -> {path.name}")

    def write_hook_updates(self, updates: dict, vault_dir: str):
        """追加新伏笔到 vault/伏笔 ledger.md。"""
        new_hooks = updates.get("new", [])
        if not new_hooks:
            return

        path = Path(vault_dir) / "伏笔 ledger.md"
        lines = ["\n", "（引擎追加）\n"]
        for h in new_hooks:
            hid = h.get("id", "?")
            desc = h.get("description", "")
            created = h.get("created_at", "?")
            lines.append(f"| {hid} | {desc} | {created} | {created} | 新增 |")

        new_content = "\n".join(lines)
        marker = "<!-- vault-hooks-insert -->"

        if path.exists():
            bak = path.with_suffix(".md.bak")
            if not bak.exists():
                path.rename(bak)
            existing = path.read_text(encoding="utf-8")

            # 优先搜 marker
            marker_pos = existing.find(marker)
            if marker_pos >= 0:
                existing = existing[:marker_pos] + new_content + "\n" + marker + existing[marker_pos + len(marker):]
            else:
                # fallback: 搜 section header
                section_end = existing.find("\n## 推进中")
                if section_end < 0:
                    section_end = existing.find("\n## 已回收")
                if section_end < 0:
                    section_end = len(existing)
                insert_at = existing.rfind("\n", 0, section_end)
                if insert_at < 0:
                    insert_at = section_end
                existing = existing[:insert_at] + new_content + "\n" + existing[insert_at:]
                # 追加 marker 以备后续
                existing += "\n" + marker
        else:
            existing = ("# 伏笔 ledger\n\n## 开放中\n\n| ID | 伏笔 | 创建章 | 最后接触 | 类型 |\n"
                        "|----|------|--------|----------|------|\n" + new_content + "\n"
                        + marker + "\n")

        path.write_text(existing, encoding="utf-8")
        log.info(f"  [vault_sync] 伏笔: 追加 {len(new_hooks)} 条 -> {path.name}")

    def write_character_updates(self, updates: dict[str, dict], vault_dir: str, arc_name: str = "unnamed"):
        """写角色状态更新到 vault/_角色更新.md（手动合并）。"""
        if not updates:
            return

        path = Path(vault_dir) / f"_角色更新_{arc_name}.md"
        lines = [
            f"# 引擎角色更新：{arc_name}",
            "",
            "> 以下为引擎自动生成的角色状态变更，请审阅后手动合并到 vault/人物/*.md 的 frontmatter。",
            "",
        ]
        for char_name, data in updates.items():
            lines.append(f"## {char_name}")
            lines.append("")
            if data.get("summary"):
                lines.append(f"- 新信念：{data['summary']}")
            if data.get("goal"):
                lines.append(f"- 新目标：{data['goal']}")
            if data.get("perception_stage"):
                lines.append(f"- 感知阶段：{data['perception_stage']}")
            if data.get("chapter"):
                lines.append(f"- 所属章节：{data['chapter']}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        log.info(f"  [vault_sync] 角色更新 -> {path.name}（请手动合并）")

    def write_to_vault(self, tick_result: dict, arc_config: dict, vault_dir: str):
        """一站式写入所有 vault 更新。"""
        # 1. 时间线
        timeline = self.build_timeline_entries(tick_result, arc_config)
        self.write_timeline(timeline, vault_dir)

        # 2. 伏笔
        hooks = self.build_hook_updates(tick_result.get("new_hooks", []))
        self.write_hook_updates(hooks, vault_dir)

        # 3. 角色更新
        char_updates = self.build_character_updates(tick_result, arc_config)
        self.write_character_updates(char_updates, vault_dir, arc_config.get("arc_id", "unnamed"))
