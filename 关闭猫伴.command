#!/bin/bash
# 双击这个文件即可停掉 NekoPal（窗口 + 后端 + 变声服务）。

cd "$(dirname "$0")" || exit 1

if [ ! -x ./.venv/bin/nekopal ]; then
  echo "❌ 找不到 ./.venv/bin/nekopal"
  exit 1
fi

./.venv/bin/nekopal down
echo
echo "全部已停止。"
sleep 3
