#!/bin/bash
# 一条命令加一个「变声音色」（RVC 模型）—— 不用手写 YAML。
#
#     ./scripts/add_voice.sh <模型.pth> [index文件] [--id 短名] [--name 显示名] [--base-voice 音色] [--license 等级]
#
# 最短用法（id 与显示名都从文件名推出来）：
#
#     ./scripts/add_voice.sh ~/Downloads/mutsumi.pth
#
# 带 index 和中文名：
#
#     ./scripts/add_voice.sh ~/Downloads/mutsumi.pth ~/Downloads/mutsumi.index \
#         --name "若葉睦（社区 RVC）"
#
# `--license` 默认 `unofficial`（社区训练产物，界面会打 ⚠️ 黄标）；
# 如果你的模型来源是合法的，请如实改（否则界面会误报）：
#
#     --license self-recorded | public-domain | licensed | official
#
# 它做四件事：① 建目录 ② 拷模型 ③ 写 voice.yaml ④ 跑 `nekopal packs list` 验证。
#
# ⚠️ 项目只提供**加载机制**，不提供任何模型 —— 模型请自行准备，授权责任也在你。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
PY="$ROOT/.venv/bin/python"

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

MODEL=""
INDEX=""
ID=""
NAME=""
BASE="edge:zh-CN-XiaoxiaoNeural"
LICENSE="unofficial"

while [ $# -gt 0 ]; do
  case "$1" in
    --id)         ID="$2"; shift 2 ;;
    --name)       NAME="$2"; shift 2 ;;
    --base-voice) BASE="$2"; shift 2 ;;
    --license)    LICENSE="$2"; shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    *)
      if [ -z "$MODEL" ]; then MODEL="$1"
      elif [ -z "$INDEX" ]; then INDEX="$1"
      else echo "多余的参数：$1" >&2; exit 2; fi
      shift ;;
  esac
done

case "$LICENSE" in
  official|self-recorded|licensed|public-domain|unofficial) ;;
  *) echo "❌ --license 只能是：official / self-recorded / licensed / public-domain / unofficial" >&2; exit 2 ;;
esac

# 商业可用性：只有「来源明确」的几类才默认放行，其余保守标 false（可在 YAML 里再改）
case "$LICENSE" in
  official|self-recorded|public-domain) COMMERCIAL="true" ;;
  *) COMMERCIAL="false" ;;
esac

[ -n "$MODEL" ] || { usage; exit 2; }
[ -f "$MODEL" ] || { echo "❌ 找不到模型文件：$MODEL" >&2; exit 1; }
if [ -n "$INDEX" ] && [ ! -f "$INDEX" ]; then
  echo "❌ 找不到 index 文件：$INDEX" >&2
  exit 1
fi

# id 默认从文件名推：mutsumi.pth → mutsumi
if [ -z "$ID" ]; then
  ID="$(basename "$MODEL")"
  ID="${ID%.*}"
fi
ID="$(printf '%s' "$ID" | tr -cs 'A-Za-z0-9_-' '-' | sed 's/^-//; s/-$//')"
[ -n "$ID" ] || { echo "❌ 推不出合法的 id，请用 --id 指定（建议用英文）" >&2; exit 1; }
[ -n "$NAME" ] || NAME="$ID"

# 音色包目录以程序为准（用户目录优先），不要自己拼路径
VOICES="$("$PY" -c 'from nekopal.packs import search_dirs; print(search_dirs()[0][0])')"
DEST="$VOICES/$ID"
if [ -f "$DEST/voice.yaml" ]; then
  echo "⚠️  $DEST 已经存在，将被覆盖（按 Ctrl+C 取消，3 秒…）"
  sleep 3
fi
mkdir -p "$DEST"

MODEL_NAME="$(basename "$MODEL")"
cp -f "$MODEL" "$DEST/$MODEL_NAME"

INDEX_LINE="  # index: （没有 index 文件就保持注释）"
if [ -n "$INDEX" ]; then
  INDEX_NAME="$(basename "$INDEX")"
  cp -f "$INDEX" "$DEST/$INDEX_NAME"
  INDEX_LINE="  index: $INDEX_NAME"
fi

# ⚠️ heredoc 的终止符那行**必须**是孤零零的 `YAML` ——
#    写成 `${INDEX_LINE}YAML` 是不行的（终止符按字面匹配，不会被展开），
#    那样会把后面的 shell 代码一起写进 YAML 里（踩过）。
cat > "$DEST/voice.yaml" <<YAML
id: $ID
name: "$NAME"
backend: rvc
lang: ["ja", "zh"]
tags: ["community"]
license: $LICENSE            # 如实标：来源不明确就保持 unofficial（界面会打 ⚠️ 黄标）
commercial_ok: $COMMERCIAL
redistributable: false
description: "仅供个人使用；不要用于公开发布或商业用途"
rvc:
  base_voice: $BASE
  model: $MODEL_NAME
$INDEX_LINE
YAML

echo "✅ 已建好音色包：$DEST"
echo "   （只提供加载机制；模型授权责任在使用者）"
echo
exec "$ROOT/.venv/bin/nekopal" packs list
