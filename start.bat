@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "ENV_FILE=%USERPROFILE%\.env"
set "OVERRIDE_FILE=%USERPROFILE%\.fictionforge_routes.json"

echo == 1/5 检查 Python ==
set PY=python
where python >nul 2>nul
if errorlevel 1 set PY=py
where %PY% >nul 2>nul
if errorlevel 1 (
  echo   没找到 Python，请先安装: https://www.python.org/downloads/  ^(勾上 Add to PATH^)
  exit /b 1
)
%PY% -c "import sys; assert sys.version_info >= (3,9)" >nul 2>nul
if errorlevel 1 (
  echo   Python 版本太低，要 3.9+
  exit /b 1
)
echo   OK Python 3.9+

echo == 2/5 装依赖 ==
%PY% -m pip install --quiet requests
if errorlevel 1 (
  echo   装 requests 失败，检查网络/镜像
  exit /b 1
)
echo   OK

echo == 3/5 配置 API key ==
if not exist "%ENV_FILE%" type nul > "%ENV_FILE%"
set has_deepseek=0
set has_dashscope=0
set has_openrouter=0
findstr /B /C:"DEEPSEEK_API_KEY=" "%ENV_FILE%" >nul 2>nul && set has_deepseek=1
findstr /B /C:"DASHSCOPE_API_KEY=" "%ENV_FILE%" >nul 2>nul && set has_dashscope=1
findstr /B /C:"OPENROUTER_API_KEY=" "%ENV_FILE%" >nul 2>nul && set has_openrouter=1
if !has_deepseek!==0 if !has_dashscope!==0 if !has_openrouter!==0 (
  echo   你的 !ENV_FILE! 还没有 key。
  echo   推荐 DeepSeek ^(国内直连，不用翻墙^): https://platform.deepseek.com 注册拿 key
  set /p KEY=  粘贴 DeepSeek API key ^(sk- 开头^):
  if "!KEY!"=="" (
    echo   没输入，退出
    exit /b 1
  )
  echo DEEPSEEK_API_KEY=!KEY!>> "%ENV_FILE%"
  set has_deepseek=1
  echo   已写入 !ENV_FILE!
)

if !has_openrouter!==1 (
  if exist "%OVERRIDE_FILE%" del "%OVERRIDE_FILE%"
  echo   检测到 OPENROUTER key → 用默认路线 ^(海外 Kimi，需科学上网^)
) else (
  if !has_deepseek!==1 (
    set PROVIDER=deepseek
    set MODEL=deepseek-v4-flash
    echo   国内模式 → 正文用 DeepSeek ^(不用翻墙^)
  ) else if !has_dashscope!==1 (
    set PROVIDER=dashscope
    set MODEL=qwen-max
    echo   国内模式 → 正文用阿里百炼 Qwen ^(不用翻墙^)
  )
  echo {> "%OVERRIDE_FILE%"
  echo   "normal": {"provider": "!PROVIDER!", "model": "!MODEL!"},>> "%OVERRIDE_FILE%"
  echo   "expanded": {"provider": "!PROVIDER!", "model": "!MODEL!"}>> "%OVERRIDE_FILE%"
  echo }>> "%OVERRIDE_FILE%"
  echo   已写 !OVERRIDE_FILE! ^(想切回海外路线就删这个文件^)
)

echo == 4/5 启动 LLM 代理 ^(端口 3002^) ==
set PROXY_RUNNING=0
%PY% scripts\_wait_proxy.py 3002 1 >nul 2>nul
if not errorlevel 1 set PROXY_RUNNING=1
if !PROXY_RUNNING!==0 (
  start "FictionForge proxy" /b %PY% server\gen_proxy.py
  %PY% scripts\_wait_proxy.py 3002 30
  if errorlevel 1 (
    echo   代理没起来
    exit /b 1
  )
  echo   OK  代理就绪
) else (
  echo   3002 已有代理在跑，跳过启动
)

echo == 5/5 跑通示例 ==
%PY% scripts\gen.py --force novels\静默轨道\specs\ch1.json

echo.
echo 完成。正文输出在: novels\静默轨道\chapters\
echo 想写自己的小说？打开 AGENTS.md 顶部「3 分钟上手」，直接问 AI 助手帮你搭内容包。
