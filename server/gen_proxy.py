#!/usr/bin/env python3
"""Proxy: gen.py custom format → OpenRouter/DeepSeek API. Uses requests (SSL-safe)."""
import json, os, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

# Auto-load ~/.env
_env_path = os.path.expanduser("~/.env")
if os.path.exists(_env_path):
    for line in open(_env_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ── Route table ──
ROUTES = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "key": os.environ.get("OPENROUTER_API_KEY", ""),
        "extra_headers": {"HTTP-Referer": "http://localhost:3002", "X-Title": "novel-writing-gen"},
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/chat/completions",
        "key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "extra_headers": {},
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "extra_headers": {},
    },
}
DEFAULT_PROVIDER = "openrouter"


class ProxyHandler(BaseHTTPRequestHandler):
    def _write_error(self, msg):
        err = json.dumps({"type": "error", "error": msg}, ensure_ascii=False)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(f"data: {err}\n".encode("utf-8"))
        self.wfile.flush()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 256 * 1024:
            self._write_error(f"Content-Length {length} exceeds 256KB limit")
            return
        body = json.loads(self.rfile.read(length))

        messages = body.get("messages", [])
        system_prompt = body.get("systemPrompt", "")
        model = body.get("model", "")
        provider = body.get("provider", DEFAULT_PROVIDER)

        route = ROUTES.get(provider)
        if not route:
            self._write_error(f"unknown provider: {provider}")
            return
        api_key = route["key"]
        if not api_key:
            self._write_error(f"no API key for provider: {provider}")
            return

        openai_messages = []
        if system_prompt:
            openai_messages.append({"role": "system", "content": system_prompt})
        openai_messages.extend(messages)

        req_body = {
            "model": model,
            "messages": openai_messages,
            "stream": True,
            "temperature": body.get("temperature", 0.85),
            "max_tokens": body.get("maxTokens", 32768),
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
        }
        headers.update(route["extra_headers"])

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        full_content = ""
        try:
            resp = requests.post(
                route["base_url"],
                json=req_body,
                headers=headers,
                stream=True,
                timeout=300,
            )
            resp.raise_for_status()

            for line_bytes in resp.iter_lines(decode_unicode=False):
                if not line_bytes:
                    continue
                try:
                    line = line_bytes.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = data.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {}) or {}
                content = delta.get("content") or ""

                if content:
                    full_content += content
                    sse = json.dumps({"type": "chunk", "content": content}, ensure_ascii=False)
                    self.wfile.write(f"data: {sse}\n".encode("utf-8"))
                    self.wfile.flush()

            done = json.dumps({"type": "done", "fullContent": full_content}, ensure_ascii=False)
            self.wfile.write(f"data: {done}\n".encode("utf-8"))
            self.wfile.flush()
        except Exception as e:
            err = json.dumps({"type": "error", "error": str(e)}, ensure_ascii=False)
            self.wfile.write(f"data: {err}\n".encode("utf-8"))
            self.wfile.flush()

    def log_message(self, format, *args):
        sys.stderr.write(f"[proxy] {args[0]} {args[1]} {args[2]}\n")


if __name__ == "__main__":
    port = 3002
    server = HTTPServer(("0.0.0.0", port), ProxyHandler)
    sys.stderr.write(f"[proxy] listening on {port}\n")
    sys.stderr.flush()
    server.serve_forever()
