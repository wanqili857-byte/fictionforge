"""
api.py — 统一 LLM 调用层。

使用 _api_client.py（共享 SSE 客户端）通信。
提供两种调用方式：
  call_llm(system, user) → str            # 纯文本输出
  call_structured(system, user, schema) → dict  # 结构化 JSON 输出
"""

import json
import re
from typing import Optional

# sys.path 由 engine.__init__.py 统一注册，本模块无需再操作
from lib.log import get_logger
log = get_logger("engine.api")
from server._api_client import sse_request, MODEL_ROUTES


def call_llm(system_prompt: str,
             user_prompt: str,
             route_key: str = "qwen-agent",
             temperature: Optional[float] = None) -> str:
    """调用 LLM 返回纯文本。空响应/超时自动重试 1 次。"""
    route = MODEL_ROUTES.get(route_key, MODEL_ROUTES["agent"])
    body = {
        "messages": [{"role": "user", "content": user_prompt}],
        "systemPrompt": system_prompt,
        "provider": route["provider"],
        "model": route["model"],
        "temperature": temperature if temperature is not None else route["temperature"],
        "maxTokens": route["maxTokens"],
    }

    text = sse_request(body)
    if text:
        return text

    # 空响应重试一次（deepseek 偶发超时/流中断）
    log.warning(f"  [api] call_llm 空响应，重试 1 次 ({route_key})")
    text = sse_request(body)
    return text


def _extract_json(text: str) -> Optional[dict]:
    """尝试多种策略从 LLM 响应中提取 JSON。

    处理截断、尾部文本、```json 块等。失败返回 None。
    """
    if not text:
        return None

    # 策略 1：直接解析（最干净的情况）
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 策略 2：```json 代码块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 策略 3：花括号提取（去掉头部/尾部多余文本）
    brace_start = text.find('{')
    if brace_start < 0:
        return None
    brace_end = text.rfind('}')
    if brace_end <= brace_start:
        return None

    candidate = text[brace_start:brace_end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 策略 4：花括号提取后修正常见截断（利用 JSONDecodeError 的 pos 定位）
    body = text[brace_start:]
    # 多轮修复：逐步修复直到成功或放弃
    for _ in range(10):
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            pos = e.pos
            if pos <= 0:
                break
            # 从错误位置开始向前找最近的换行或逗号，截掉之后所有内容
            cut = body.rfind('\n', 0, pos)
            if cut < 0:
                cut = body.rfind(',', 0, pos)
            if cut < 0:
                cut = pos
            body = body[:cut].rstrip(',') + '\n}'
            # 如果关闭括号过多，尝试只保留一个
            open_br = body.count('{') - body.count('}')
            open_sq = body.count('[') - body.count(']')
            while open_br < 0:
                body = '{' + body
                open_br += 1
            while open_sq < 0:
                body = '[' + body
                open_sq += 1
            body += '}' * open_br + ']' * open_sq

    return None


def call_structured(system_prompt: str,
                    user_prompt: str,
                    output_schema: dict,
                    route_key: str = "qwen-agent",
                    temperature: Optional[float] = 0.3) -> dict:
    """调用 LLM 返回结构化 JSON 输出。

    在 system prompt 末尾追加 JSON schema 约束，
    解析响应并返回 dict。解析失败时返回空 dict。
    """
    schema_text = json.dumps(output_schema, ensure_ascii=False, indent=2)
    schema_instruction = (
        f"\n\n你必须输出严格的 JSON，符合以下 schema：\n{schema_text}\n"
        f"只输出 JSON 对象，不要输出其他内容。不要用 ```json 包裹。"
    )
    full_system = system_prompt + schema_instruction

    # 重试机制：LLM 偶发超时/空响应/解析失败（deepseek 常见）
    # 失败后重试 2 次，重试时提示上一次失败原因
    MAX_RETRIES = 2
    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            log.warning(f"  [api] call_structured 第{attempt}次重试 ({route_key})")
        text = call_llm(full_system, user_prompt, route_key, temperature)
        if not text:
            continue
        result = _extract_json(text)
        if result:
            return result
        log.warning(f"无法解析结构化输出(尝试{attempt + 1}):\n{text[:300]}")

    return {}
