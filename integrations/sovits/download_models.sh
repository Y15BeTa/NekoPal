#!/bin/bash
# 下载 GPT-SoVITS 推理需要的基础模型（位置照官方 install.sh，放错就找不到）。
#
# 用法：
#   bash integrations/sovits/download_models.sh
#   bash integrations/sovits/download_models.sh --mirror      # 走 hf-mirror.com
#   bash integrations/sovits/download_models.sh --modelscope  # 走国内 ModelScope
#
# ⚠️ 只下**推理 + 日语**必需的三个：
#      pretrained_models（hubert / roberta / sv）· G2PWModel · open_jtalk 词典
#    uvr5_weights（人声分离）用不到，不下。
#    nltk_data（英文 g2p）让 g2p_en 自己按需下。
#
# 本项目不提供也不分发任何模型，这里只是按官方地址拉取。

set -u

MODE="hf"
for arg in "$@"; do
  case "$arg" in
    --mirror) MODE="mirror" ;;
    --modelscope) MODE="modelscope" ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
  esac
done

case "$MODE" in
  mirror)     BASE="https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main" ;;
  modelscope) BASE="https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master" ;;
  *)          BASE="https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main" ;;
esac

SOVITS="${NEKOPAL_SOVITS_ENGINE:-$HOME/.local/share/nekopal/sovits}"
VENV="${NEKOPAL_SOVITS_VENV:-$HOME/.local/share/nekopal/sovits-venv}"
WORK="${TMPDIR:-/tmp}/sovits-models"

if [ ! -d "$SOVITS/GPT_SoVITS" ]; then
  echo "✗ 找不到 GPT-SoVITS 本体：$SOVITS"
  echo "  先 git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS.git \"$SOVITS\""
  exit 1
fi

mkdir -p "$WORK" || exit 1
cd "$WORK" || exit 1
echo "来源：$BASE"
echo "本体：$SOVITS"
echo

fetch() {  # fetch <文件名>
  local name="$1"
  if is_complete "$name"; then
    echo "  ⏭  $name 已存在且完整（$(du -h "$name" | cut -f1)）"
    return 0
  fi
  [ -e "$name" ] && echo "  ⚠️  $name 之前是半截的（断流了），重下" && rm -f "$name"
  echo "  ⬇  $name …"
  curl -L --retry 5 --retry-delay 3 -C - -o "$name" "$BASE/$name" 2>/dev/null || true
  if is_complete "$name"; then
    echo "     ✅ $(du -h "$name" | cut -f1)"
  else
    echo "     ✗ 还是没下全（换个源再试：--mirror / --modelscope）"
    rm -f "$name"
    return 1
  fi
}

#: ⚠️ **非空 ≠ 完整**。断点续传中途断掉会留一个"看起来像下载成功"的半截文件 ——
#: 最要命的不是多下一次，而是**下次重跑会因为"文件存在"而跳过它**。
#: 所以这里真的去读一遍：能完整读完才算下好。
is_complete() {
  local f="$1"
  case "$f" in
    *.zip) [ -s "$f" ] && unzip -tq "$f" >/dev/null 2>&1 ;;
    *.tar.gz) [ -s "$f" ] && tar -tzf "$f" >/dev/null 2>&1 ;;
    *) [ -s "$f" ] ;;
  esac
}

echo "════ ① 下载 ════"
fetch pretrained_models.zip
fetch G2PWModel.zip
fetch open_jtalk_dic_utf_8-1.11.tar.gz
echo

echo "════ ② 解压到该去的地方 ════"

if [ ! -d "$SOVITS/GPT_SoVITS/pretrained_models/sv" ]; then
  echo "  → pretrained_models → GPT_SoVITS/"
  unzip -q -o pretrained_models.zip -d "$SOVITS/GPT_SoVITS" && echo "     ✅" || echo "     ✗"
else
  echo "  ⏭  pretrained_models 已就位"
fi

if [ ! -d "$SOVITS/GPT_SoVITS/text/G2PWModel" ]; then
  echo "  → G2PWModel → GPT_SoVITS/text/"
  unzip -q -o G2PWModel.zip -d "$SOVITS/GPT_SoVITS/text" && echo "     ✅" || echo "     ✗"
else
  echo "  ⏭  G2PWModel 已就位"
fi

# ⚠️ open_jtalk 词典必须放进 **pyopenjtalk 包自己的目录里** ——
#    代码（GPT_SoVITS/text/japanese.py）是从那里找它的，放别处等于没放。
JTDIR="$("$VENV/bin/python" -c "import os,pyopenjtalk;print(os.path.dirname(pyopenjtalk.__file__))" 2>/dev/null)"
if [ -n "$JTDIR" ]; then
  if [ ! -d "$JTDIR/open_jtalk_dic_utf_8-1.11" ]; then
    echo "  → open_jtalk 词典 → $JTDIR"
    tar -xzf open_jtalk_dic_utf_8-1.11.tar.gz -C "$JTDIR" && echo "     ✅" || echo "     ✗"
  else
    echo "  ⏭  open_jtalk 词典已就位"
  fi
else
  echo "  ⚠️ pyopenjtalk 还没装上（依赖没装完？）→ 装完后重跑本脚本即可"
fi

echo
echo "════ ③ 结果 ════"
ls "$SOVITS/GPT_SoVITS/pretrained_models/" 2>/dev/null | sed 's/^/  models: /'
du -sh "$SOVITS" 2>/dev/null | sed 's/^/  引擎总体积: /'
echo "DONE"
