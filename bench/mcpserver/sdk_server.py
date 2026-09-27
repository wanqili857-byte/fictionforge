#!/usr/bin/env python3
"""bench.mcpserver.sdk_server — 官方 MCP SDK 版实现（mcp ≥2.0，需 Python ≥3.10）。

与手写版（server.py）共存，各自有理由：
  - 本文件：标准实现，功能全（resources/prompts/HTTP 传输/取消/并发），依赖较重
  - server.py：零依赖手写协议层，3.9 可用，协议级可单测、可审计

工具逻辑不分叉——两版都调 tools.py 的同一批纯函数；
描述文本也从 tool_specs() 取，避免两处漂移。
"""

import sys
from pathlib import Path
from typing import Optional

_SELF = Path(__file__).resolve()
if str(_SELF.parent.parent.parent) not in sys.path:
    sys.path.insert(0, str(_SELF.parent.parent.parent))

from bench.mcpserver.tools import call_tool, tool_specs
from bench.mcpserver.server import SERVER_NAME, SERVER_VERSION

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError as SDKToolError
except ImportError:      # 未装 SDK / Python < 3.10
    MCPServer = None
    SDKToolError = None

INSTRUCTIONS = (
    "LCB · 长程叙事一致性基准。提供合成宇宙生成、状态账本、真相表、"
    "门禁（机械+状态判定）与草稿生成五个工具。除 novel_gen 外均零 LLM、确定性。"
)


def _desc(name: str) -> str:
    for t in tool_specs():
        if t["name"] == name:
            return t["description"]
    return ""


def _wrap(name: str, args: dict):
    """统一错误语义：ToolError → SDK 的 ToolError（客户端收到 isError 结果）。

    注意不能抛 ValueError 等普通异常——SDK 会把它当「未预期异常」
    （UnexpectedToolError），在客户端侧表现为服务端错误而非工具错误。
    """
    try:
        return call_tool(name, args)
    except Exception as e:
        raise SDKToolError(str(e))


def build_server():
    """构造 MCPServer 并注册五个工具（返回实例，供测试与 run 使用）。"""
    if MCPServer is None:
        raise RuntimeError("未安装官方 mcp SDK（pip install mcp，需 Python ≥3.10）")

    server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION,
                       instructions=INSTRUCTIONS)

    @server.tool(name="universe_generate", description=_desc("universe_generate"))
    def universe_generate(seed: int, chapters: int = 12,
                          out_dir: Optional[str] = None) -> dict:
        return _wrap("universe_generate",
                     {"seed": seed, "chapters": chapters, "out_dir": out_dir})

    @server.tool(name="novel_ledger", description=_desc("novel_ledger"))
    def novel_ledger(novel_dir: str, protagonist: Optional[str] = None) -> dict:
        return _wrap("novel_ledger",
                     {"novel_dir": novel_dir, "protagonist": protagonist})

    @server.tool(name="novel_truth", description=_desc("novel_truth"))
    def novel_truth(novel_dir: str, only_false: bool = False) -> dict:
        return _wrap("novel_truth",
                     {"novel_dir": novel_dir, "only_false": only_false})

    @server.tool(name="novel_gate", description=_desc("novel_gate"))
    def novel_gate(novel_dir: str, chapter: int,
                   text: Optional[str] = None,
                   text_path: Optional[str] = None) -> dict:
        return _wrap("novel_gate",
                     {"novel_dir": novel_dir, "chapter": chapter,
                      "text": text, "text_path": text_path})

    @server.tool(name="novel_gen", description=_desc("novel_gen"))
    def novel_gen(spec: str, resection: Optional[str] = None,
                  force: bool = False) -> dict:
        return _wrap("novel_gen",
                     {"spec": spec, "resection": resection, "force": force})

    return server


def run() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    run()