#!/usr/bin/env python3
"""LCB MCP server 启动器 — 供 Claude Code / 其他 MCP 客户端注册。

注册（用户级）:
    claude mcp add lcb -- python3 /Users/ayu/ayu/写作/fictionforge/scripts/lcb_mcp.py

注册（项目级 .mcp.json）:
    {"mcpServers": {"lcb": {"command": "python3",
                            "args": ["/Users/ayu/ayu/写作/fictionforge/scripts/lcb_mcp.py"]}}}
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.mcpserver.server import serve

if __name__ == "__main__":
    serve()