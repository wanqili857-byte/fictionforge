#!/usr/bin/env bash
# FictionForge 一键启动（macOS / Linux）
# 用法：在项目根目录执行  ./start.sh
# 自动：检查 Python → 装依赖 → 配 key → 起代理 → 跑通示例
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
ENV_FILE="$HOME/.env"
OVERRIDE_FILE="$HOME/.fictionforge_routes.json"

echo "== 1/5 检查 Python =="
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "  没找到 $PY，请先安装: https://www.python.org/downloads/  (安装时勾上 Add to PATH / 通用)"
  exit 1
fi
VER=$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
MAJOR=${VER%%.*}; MINOR=${VER#*.}
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 9 ]; }; then
  echo "  Python 版本太低（$VER），要 3.9+"
  exit 1
fi
echo "  OK  Python $VER"

echo "== 2/5 装依赖 =="
"$PY" -m pip install --quiet requests || { echo "  装 requests 失败，检查网络/镜像"; exit 1; }
echo "  OK"

echo "== 3/5 配置 API key =="
touch "$ENV_FILE"
has_deepseek=0; has_dashscope=0; has_openrouter=0
grep -q "^DEEPSEEK_API_KEY=." "$ENV_FILE" && has_deepseek=1
grep -q "^DASHSCOPE_API_KEY=." "$ENV_FILE" && has_dashscope=1
grep -q "^OPENROUTER_API_KEY=." "$ENV_FILE" && has_openrouter=1
if [ "$has_deepseek" = 0 ] && [ "$has_dashscope" = 0 ] && [ "$has_openrouter" = 0 ]; then
  echo "  你的 $ENV_FILE 还没有 key。"
  echo "  推荐 DeepSeek（国内直连，不用翻墙）：https://platform.deepseek.com 注册拿 key"
  read -rp "  粘贴 DeepSeek API key（sk- 开头）: " KEY
  [ -z "$KEY" ] && { echo "  没输入，退出"; exit 1; }
  printf 'DEEPSEEK_API_KEY=%s\n' "$KEY" >> "$ENV_FILE"
  has_deepseek=1
  echo "  已写入 $ENV_FILE"
fi

# 国内路线：只有国内 key 时，把正文生成（normal/expanded）切到国内供应商。
# 写入 ~/.fictionforge_routes.json，之后手动跑 gen.py 也自动生效。
if [ "$has_openrouter" = 1 ]; then
  rm -f "$OVERRIDE_FILE"
  echo "  检测到 OPENROUTER key → 用默认路线（海外 Kimi，需科学上网）"
else
  if [ "$has_deepseek" = 1 ]; then
    PROVIDER=deepseek; MODEL=deepseek-v4-flash
    echo "  国内模式 → 正文用 DeepSeek（不用翻墙）"
  elif [ "$has_dashscope" = 1 ]; then
    PROVIDER=dashscope; MODEL=qwen-max
    echo "  国内模式 → 正文用阿里百炼 Qwen（不用翻墙）"
  else
    PROVIDER=deepseek; MODEL=deepseek-v4-flash
  fi
  if [ -n "$PROVIDER" ]; then
    cat > "$OVERRIDE_FILE" <<EOF
{"normal": {"provider": "$PROVIDER", "model": "$MODEL"},
 "expanded": {"provider": "$PROVIDER", "model": "$MODEL"}}
EOF
    echo "  已写 $OVERRIDE_FILE（想切回海外路线就删这个文件）"
  fi
fi

echo "== 4/5 启动 LLM 代理（端口 3002） =="
if "$PY" scripts/_wait_proxy.py 3002 1 >/dev/null 2>&1; then
  echo "  3002 已有代理在跑，跳过启动"
else
  "$PY" server/gen_proxy.py >/tmp/ff_proxy.log 2>&1 &
  "$PY" scripts/_wait_proxy.py 3002 30 || { echo "  代理没起来，日志: /tmp/ff_proxy.log"; exit 1; }
  echo "  OK  代理就绪（日志 /tmp/ff_proxy.log，停止: pkill -f gen_proxy.py）"
fi

echo "== 5/5 跑通示例 =="
"$PY" scripts/gen.py --force novels/静默轨道/specs/ch1.json

echo ""
echo "完成。正文输出在: novels/静默轨道/chapters/"
echo "想写自己的小说？打开 AGENTS.md 顶部「3 分钟上手」，直接问 AI 助手帮你搭内容包。"
