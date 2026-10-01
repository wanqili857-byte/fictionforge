#!/usr/bin/env python3
"""已改名：脚本现在是 scripts/canonbench_mcp.py（LCB → CanonBench）。

保留此文件只为不打断已注册的 MCP 配置；请在方便时改指新路径：
    claude mcp remove lcb && claude mcp add canonbench -- python3 scripts/canonbench_mcp.py
"""

import runpy
import sys
from pathlib import Path

sys.stderr.write("[lcb_mcp] 已改名为 canonbench_mcp.py（此 shim 可随时删除）\n")
runpy.run_path(str(Path(__file__).with_name("canonbench_mcp.py")), run_name="__main__")
