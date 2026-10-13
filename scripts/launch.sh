#!/bin/bash
# 启动 NekoPal：变声服务（若已配置）→ 后端 → 原生窗口。
#
# 三种入口都走这里，避免逻辑复制三份：
#   · 双击 `启动猫伴.command`
#   · 双击 `启动猫伴.app`（由 scripts/install_macos_apps.sh 生成）
#   · 手工 `./scripts/launch.sh`
#
# 幂等：已经在跑就直接结束。

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

LOG_DIR="$HOME/Library/Logs/NekoPal"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/launch.log"

# ⚠️ 双击 .app / .command 时环境极简，**不会读 ~/.zshrc** —— 必须自己把需要的变量捞出来。
# 只取 SDKROOT / NEKOPAL_*，避免 .zshrc 里的 zsh 专属语法在 bash 里报错。
if [ -f "$HOME/.zshrc" ]; then
  eval "$(grep -E '^export (SDKROOT|NEKOPAL_)' "$HOME/.zshrc")" 2>/dev/null || true
fi

if [ ! -x "$ROOT/.venv/bin/nekopal" ]; then
  {
    echo "❌ 找不到 $ROOT/.venv/bin/nekopal"
    echo "   先在 $ROOT 执行："
    echo "   uv venv --python 3.12 && uv pip install -e \".[edge,dev,desktop]\""
  } 2>&1 | tee -a "$LOG"
  exit 1
fi

# 已经在跑 → 什么都不做（窗口若已开着就保持原样）
if curl -sf --max-time 1 http://127.0.0.1:8787/api/health >/dev/null 2>&1; then
  echo "NekoPal 已在运行（无需重复启动）" | tee -a "$LOG"
  exit 0
fi

# 上游全部委托给 CLI：变声服务 → 后端 → 窗口
exec "$ROOT/.venv/bin/nekopal" up
