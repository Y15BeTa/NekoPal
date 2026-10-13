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

# ⚠️ 判断"是否已经在运行"要看**窗口进程**，不能看"后端在不在"。
#    后端可能是别的方式起的（手动 nohup / 上一次没关干净），
#    那时窗口并没有开 —— 用后端来判断会导致"App 拒绝开窗、什么也听不到"（踩过）。
window_running() {
  pgrep -f "nekopal up" >/dev/null 2>&1 || pgrep -f "nekopal app" >/dev/null 2>&1
}

if window_running; then
  echo "NekoPal 窗口已经在运行（无需重复打开）" | tee -a "$LOG"
  exit 0
fi

# 上游全部委托给 CLI：变声服务 → 后端（已在跑就复用）→ 窗口
exec "$ROOT/.venv/bin/nekopal" up
