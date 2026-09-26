#!/usr/bin/env python3
"""bench.runner.llm — 真实 LLM 调用（OpenAI 兼容接口），带 token 计量。

设计：请求体构造与响应解析是**纯函数**（可离线单测），网络调用单独一层。
key 从 ~/.env 读（与 server/gen_proxy.py 同一约定，不进仓库）。
"""

import json
import os
from typing import Optional

from bench.runner.models import ModelSpec

_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
}
_KEY_NAMES = {
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def load_keys(env_path: Optional[str] = None) -> dict:
    """从 ~/.env 读 key（只读需要的两个变量名，不打印值）。"""
    path = env_path or os.path.expanduser("~/.env")
    keys = {}
    if not os.path.exists(path):
        return keys
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in _KEY_NAMES.values():
            keys[k] = v
    return keys


def build_request_body(spec: ModelSpec, system: str, user: str) -> dict:
    """OpenAI 兼容请求体（纯函数）。"""
    return {
        "model": spec.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": spec.temperature,
        "max_tokens": spec.max_tokens,
    }


def parse_response(payload: dict) -> dict:
    """解析响应（纯函数）：返回 text / tokens_in / tokens_out / error。"""
    if not isinstance(payload, dict):
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": "响应不是 JSON 对象"}
    if "error" in payload and payload["error"]:
        err = payload["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return {"text": "", "tokens_in": 0, "tokens_out": 0, "error": msg}
    try:
        text = payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"响应结构异常: {e}"}
    usage = payload.get("usage") or {}
    return {
        "text": text.strip(),
        "tokens_in": int(usage.get("prompt_tokens") or 0),
        "tokens_out": int(usage.get("completion_tokens") or 0),
        "error": None,
    }


def call_model(spec: ModelSpec, system: str, user: str,
               keys: Optional[dict] = None, timeout: int = 240) -> dict:
    """真实调用（网络层）。返回 {text, tokens_in, tokens_out, error, cost}。"""
    import requests      # 仓库既有依赖；延迟导入便于离线测试

    keys = keys or load_keys()
    key = keys.get(_KEY_NAMES.get(spec.provider, ""), "")
    if not key:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"缺少 key: {_KEY_NAMES.get(spec.provider)}", "cost": 0.0}
    url = _BASE_URLS.get(spec.provider)
    if not url:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"未知 provider: {spec.provider}", "cost": 0.0}
    try:
        r = requests.post(url, timeout=timeout,
                          headers={"Content-Type": "application/json",
                                   "Authorization": f"Bearer {key}"},
                          json=build_request_body(spec, system, user))
    except Exception as e:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"{type(e).__name__}: {e}", "cost": 0.0}

    if r.status_code != 200:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"HTTP {r.status_code}: {r.text[:200]}", "cost": 0.0}
    try:
        out = parse_response(r.json())
    except Exception as e:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"响应解析失败: {e}", "cost": 0.0}
    out["cost"] = spec.cost(out["tokens_in"], out["tokens_out"])
    return out