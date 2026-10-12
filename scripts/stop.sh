#!/bin/bash
# 停掉 NekoPal：原生窗口 + 后端 + 变声服务。
#
# 入口：双击 `关闭猫伴.command` / `关闭猫伴.app`，或手工 `./scripts/stop.sh`。

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

if [ ! -x "$ROOT/.venv/bin/nekopal" ]; then
  echo "❌ 找不到 $ROOT/.venv/bin/nekopal"
  exit 1
fi

exec "$ROOT/.venv/bin/nekopal" down
