#!/bin/bash
# 暂停 / 继续 NekoPal 的朗读（不用把 NekoPal 窗口切到前面）。
#
# 双击本文件，或把它绑到 macOS 快捷键（见 README「全局快捷键」一节）。
#
# 用法：
#   ./scripts/toggle.command            # 暂停 / 继续（来回切）
#   ./scripts/toggle.command pause      # 只暂停
#   ./scripts/toggle.command resume     # 只继续
#   ./scripts/toggle.command stop       # 停止
#   ./scripts/toggle.command next|prev  # 下一句 / 上一句

cd "$(dirname "$0")/.." || exit 1

ACTION="${1:-toggle}"

if [ ! -x ./.venv/bin/nekopal ]; then
  echo "❌ 找不到 ./.venv/bin/nekopal"
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

./.venv/bin/nekopal control "$ACTION"

# 双击运行时留一下，好让用户看到结果；从快捷键调用时没有终端，无副作用
if [ -t 0 ]; then
  sleep 1
fi
