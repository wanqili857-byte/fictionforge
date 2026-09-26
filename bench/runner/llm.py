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
    # 火山方舟 coding plan（OpenAI 兼容端点）。注意路径是 /api/coding/v3，
    # 不是 /api/v3——后者不认 coding plan 的订阅模型（返回 NotFound）。
    "ark": "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
    # 阿里云百炼 OpenAI 兼容端点（聚合渠道：qwen 官方 + kimi/glm/minimax 等三方）
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
}
_KEY_NAMES = {
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "ark": "ARK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
}

# 代理策略：**必须显式写**，不能靠环境默认。
# requests 的 trust_env 默认会继承 macOS 系统代理，把国内域名也一起劫走——
# 本机实测：方舟经系统代理 127.0.0.1:7897 握手直接 EOF（而 curl 不读系统代理，所以 curl 能通，
# 于是这个坑只在「代码里跑」时出现，最容易误判成 TLS 版本问题）。
# 换机器复现时用 LCB_PROXY 覆盖（direct 或一个 http://host:port）。
_PROXY_POLICY = {
    "ark": "direct",        # 国内直连
    "deepseek": "direct",   # 国内直连
    "dashscope": "direct",  # 国内直连
    "openrouter": "system", # 需要代理出海
}


def resolve_proxy(provider: str, env: Optional[dict] = None) -> dict:
    """纯函数：算出该通道的代理配置 → {trust_env, proxies, source}。

    LCB_PROXY 优先级最高：
    - `LCB_PROXY=direct`        全部直连
    - `LCB_PROXY=http://h:p`    全部走该代理
    """
    env = os.environ if env is None else env
    override = (env.get("LCB_PROXY") or "").strip()
    if override:
        if override.lower() == "direct":
            return {"trust_env": False, "proxies": None, "source": "LCB_PROXY=direct"}
        return {"trust_env": False, "proxies": {"http": override, "https": override},
                "source": "LCB_PROXY"}
    if _PROXY_POLICY.get(provider, "system") == "direct":
        return {"trust_env": False, "proxies": None, "source": "policy:direct"}
    return {"trust_env": True, "proxies": None, "source": "policy:system"}


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
    """解析响应（纯函数）：返回 text / tokens_in / tokens_out / error。

    **空正文必须算 error**：思考型模型会把 max_tokens 全烧在推理上、
    正文为空（实测 glm-5.3-flash 在 8192 上三章里空了两章）。空文本若照常
    入库，判定器对空文本判 0 违反——白拿第一名。这是筛选实测揪出的洞。
    """
    if not isinstance(payload, dict):
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": "响应不是 JSON 对象"}
    if "error" in payload and payload["error"]:
        err = payload["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return {"text": "", "tokens_in": 0, "tokens_out": 0, "error": msg}
    try:
        choice = payload["choices"][0]
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"响应结构异常: {e}"}
    if not text.strip():
        finish = choice.get("finish_reason")
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"正文为空（finish_reason={finish}，"
                         f"推理占满 max_tokens？）"}
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
    px = resolve_proxy(spec.provider)
    try:
        sess = requests.Session()
        sess.trust_env = px["trust_env"]      # 关掉系统代理继承（国内通道必关）
        r = sess.post(url, timeout=timeout, proxies=px["proxies"],
                      headers={"Content-Type": "application/json",
                               "Authorization": f"Bearer {key}"},
                      json=build_request_body(spec, system, user))
    except Exception as e:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"{type(e).__name__}: {e}", "cost": 0.0}

    if r.status_code != 200:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"HTTP {r.status_code}: {r.text[:200]}", "cost": 0.0,
                "proxy": px["source"]}
    try:
        out = parse_response(r.json())
    except Exception as e:
        return {"text": "", "tokens_in": 0, "tokens_out": 0,
                "error": f"响应解析失败: {e}", "cost": 0.0, "proxy": px["source"]}
    out["cost"] = spec.cost(out["tokens_in"], out["tokens_out"])
    out["proxy"] = px["source"]
    return out