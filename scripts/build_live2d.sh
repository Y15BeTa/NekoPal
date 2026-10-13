#!/usr/bin/env bash
# 把 integrations/live2d/nekopalpet.ts 打包成 ~/.config/nekopal/l2d/pet.js
#
# 为什么需要构建：Live2D 官方框架是 TypeScript、而且内部 import **不带扩展名**
# （浏览器直接加载不了），所以要用打包器（esbuild）打成一个 ES module。
#
# 前置：
#   1. brew install node          （或任何 Node 18+）
#   2. npm i -g esbuild
#   3. 从 Live2D 官网下载 Cubism SDK for Web，解压到 $SDK_DIR
#
# 为什么产物不进仓库：pet.js 里打包了 Live2D 官方的 Cubism 5 框架，
# 与 Cubism Core 同属 Live2D 组件，一律只放本机（~/.config/nekopal/）。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_DIR="${SDK_DIR:-$HOME/.cache/nekopal/sdk-build/CubismSdkForWeb-5-r.5}"
OUT="${OUT:-$HOME/.config/nekopal/l2d}"

[ -d "$SDK_DIR/Framework/src" ] || {
  echo "找不到 SDK：$SDK_DIR" >&2
  echo "请先下载 Cubism SDK for Web 并解压，或用 SDK_DIR=... 指定" >&2
  exit 1
}
command -v esbuild >/dev/null || { echo "缺 esbuild：npm i -g esbuild" >&2; exit 1; }

mkdir -p "$OUT/shaders"
cp "$REPO/integrations/live2d/nekopalpet.ts" "$SDK_DIR/Framework/src/nekopalpet.ts"

(
  cd "$SDK_DIR/Framework/src"
  esbuild nekopalpet.ts --bundle --format=esm --target=es2020 --outfile="$OUT/pet.js"
)

# 着色器是框架**运行时 fetch** 的
cp "$SDK_DIR/Framework/Shaders/WebGL/"*.vert "$SDK_DIR/Framework/Shaders/WebGL/"*.frag "$OUT/shaders/"

echo "✅ 产物：$OUT/pet.js（$(du -h "$OUT/pet.js" | cut -f1)）+ $(ls "$OUT/shaders" | wc -l | tr -d ' ') 个着色器"
