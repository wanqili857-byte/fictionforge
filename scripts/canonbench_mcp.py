#!/usr/bin/env python3
"""CanonBench MCP server 启动器 — 供 Claude Code / 其他 MCP 客户端注册。

两个实现自动择一：
  SDK 版（bench/mcpserver/sdk_server.py）   官方 mcp SDK，Python ≥3.10，功能全
  手写版（bench/mcpserver/server.py）       零依赖协议层，3.9 可用，可审计

注册（用户级）:
    claude mcp add canonbench -- /Users/ayu/ayu/写作/fictionforge/.venv/bin/python \
        /Users/ayu/ayu/写作/fictionforge/scripts/canonbench_mcp.py
  或（无 venv / Python 3.9，走手写版）:
    claude mcp add canonbench -- python3 /Users/ayu/ayu/写作/fictionforge/scripts/canonbench_mcp.py

强制指定： --sdk / --hand-rolled
注意：stdout 是协议流，任何日志必须写 stderr。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sdk_available() -> bool:
    if sys.version_info < (3, 10):
        return False
    try:
        import mcp.server.mcpserver  # noqa: F401
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="CanonBench MCP server")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--sdk", action="store_true", help="强制官方 SDK 版")
    g.add_argument("--hand-rolled", action="store_true", help="强制零依赖手写版")
    args = ap.parse_args()

    use_sdk = args.sdk or (_sdk_available() and not args.hand_rolled)
    if use_sdk:
        try:
            from bench.mcpserver.sdk_server import run
            sys.stderr.write("[canonbench-mcp] 实现: 官方 SDK (mcp)\n")
            sys.stderr.flush()
            run()
            return 0
        except Exception as e:      # SDK 出问题 → 回落手写版，别让客户端挂掉
            sys.stderr.write(f"[canonbench-mcp] SDK 版启动失败({e})，回落手写版\n")
            sys.stderr.flush()

    from bench.mcpserver.server import serve
    sys.stderr.write("[canonbench-mcp] 实现: 手写协议层（零依赖）\n")
    sys.stderr.flush()
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())