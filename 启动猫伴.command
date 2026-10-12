#!/bin/bash
# 双击这个文件即可启动 NekoPal（变声服务 + 后端 + 原生窗口）。
#
# 真正的逻辑都在 scripts/launch.sh —— 这里只负责「后台跑 + 给点反馈 + 不占着终端」。
# 更省事的入口：跑一次 scripts/install_macos_apps.sh，
# 桌面上就会出现双击即用的「启动猫伴.app」（不弹终端、有图标）。

cd "$(dirname "$0")" || exit 1
mkdir -p /tmp/nekopal

nohup ./scripts/launch.sh > /tmp/nekopal/launch.log 2>&1 &
disown

echo "NekoPal 正在启动…（变声服务首次加载模型可能要十几秒）"
for _ in $(seq 1 60); do
  curl -sf --max-time 1 http://127.0.0.1:8787/api/health >/dev/null 2>&1 && break
  sleep 1
done
sleep 1

echo "── 启动日志 ──"
tail -12 /tmp/nekopal/launch.log 2>/dev/null || true
echo "─────────────"
echo "窗口应该已经出现了。这个终端窗口可以直接关掉。"
sleep 2
