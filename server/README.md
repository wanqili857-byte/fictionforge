# server/ — LLM 本地代理

框架与上游 LLM 供应商之间的 SSE 本地代理。gen.py 和 engine 都通过它调用模型，不在项目里留任何 API key。

## 组成 / Components

- `gen_proxy.py` — HTTP 代理（端口 **3002**），SSE 流式转发到 OpenRouter / DeepSeek / DashScope。接收请求体格式：`{ provider, model, systemPrompt, messages, temperature, maxTokens }`
- `_api_client.py` — SSE 客户端（`sse_request`），gen.py 与 `framework/api.py` 共用；`MODEL_ROUTES` 是**模型路由的唯一权威来源**

## 启动 / Run

```bash
python server/gen_proxy.py        # listening on 0.0.0.0:3002
```

## Key 配置 / Keys

代理自动读取 `~/.env`（项目外），或直接导出环境变量：

```bash
OPENROUTER_API_KEY=...   # 正文生成（normal/expanded）
DEEPSEEK_API_KEY=...     # outline / agent / state
DASHSCOPE_API_KEY=...    # 可选：qwen 路由
```

请求体里没传 `provider` 时默认 `openrouter`。

## 改模型 / Changing models

只改 `_api_client.py` 的 `MODEL_ROUTES`（provider / model / temperature / maxTokens）。新增路由就在这里加一行，gen.py 和 engine 自动生效。请求体大小上限 256KB。
