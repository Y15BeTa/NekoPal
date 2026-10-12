#!/bin/bash
# 双击这个文件即可启动 NekoPal（含变声服务与原生窗口）。
#
# 为什么不设开机自启：变声服务带 torch，常驻要几百 MB —— 不值得为它拖慢开机。
# 用完双击「关闭猫伴.command」，或运行 nekopal down。

cd "$(dirname "$0")" || exit 1

# ⚠️ 双击时用的是 bash，不会读 ~/.zshrc —— 必须自己把需要的环境变量捞出来。
# （只取我们关心的几行，避免 .zshrc 里的 zsh 专属语法在 bash 里报错）
if [ -f "$HOME/.zshrc" ]; then
  eval "$(grep -E '^export (SDKROOT|NEKOPAL_)' "$HOME/.zshrc")" 2>/dev/null || true
fi

if [ ! -x ./.venv/bin/nekopal ]; then
  echo "❌ 找不到 ./.venv/bin/nekopal"
  echo "   先在本目录执行： uv venv --python 3.12 && uv pip install -e \".[edge,dev,desktop]\""
  echo
  read -n 1 -s -r -p "按任意键关闭…"
  exit 1
fi

mkdir -p /tmp/nekopal
nohup ./.venv/bin/nekopal up > /tmp/nekopal/up.log 2>&1 &
disown

echo "NekoPal 正在启动…（变声服务首次加载模型可能要十几秒）"
echo

for i in $(seq 1 40); do
  if curl -sf --max-time 1 http://127.0.0.1:8787/api/health >/dev/null 2>&1; then
    echo "✅ 后端已就绪"
    break
  fi
  sleep 1
done

sleep 2
echo "── 启动日志 ──"
tail -12 /tmp/nekopal/up.log
echo "─────────────"
echo
echo "窗口应该已经出现了。"
echo "关闭：双击「关闭猫伴.command」"
echo "这个终端窗口可以直接关掉。"
sleep 3
