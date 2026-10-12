#!/bin/bash
# 双击这个文件即可停掉 NekoPal（原生窗口 + 后端 + 变声服务）。
#
# 逻辑在 scripts/stop.sh；桌面版是「关闭猫伴.app」。

cd "$(dirname "$0")" || exit 1
./scripts/stop.sh
echo
sleep 2
