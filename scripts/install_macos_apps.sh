#!/bin/bash
# 在 macOS 上装一个「像真正的应用一样」的 NekoPal：
#
#     ./scripts/install_macos_apps.sh          # 装到 ~/Applications（Launchpad 里可见）
#     ./scripts/install_macos_apps.sh --remove # 卸载
#
# 产出：~/Applications/NekoPal.app
#
# 设计取舍：
#   · **只有一个 App** —— 双击即开；**关掉窗口就全关**（后端与变声服务一起停，
#     由 `nekopal up` 自己收尾）。所以不需要单独的「关闭」App。
#   · **不放桌面**：桌面放着乱，Launchpad / 聚焦搜索里能找到就够了。
#   · 图标是脚本自己画的（`make_icon.py`，纯标准库），**不引用任何第三方素材**。
#
# ⚠️ 踩过的坑（很重要，别改回去）：
#   App 里**不能**去执行 `scripts/launch.sh` 这类位于 `~/Documents` 下的脚本 ——
#   macOS 会报 `Operation not permitted`（同样的事在 /tmp 下的 App 里却没事，
#   根因未明，疑似 TCC/权限的代理进程判定）。
#   解法：把启动命令**内联**进 `/bin/bash -c '...'`，完全不读那个脚本文件。
#   下面 `shellCmd` 那一行就是 `scripts/launch.sh` 的等价内联版本，改逻辑时两边一起改。

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPS="$HOME/Applications"
APP="$APPS/NekoPal.app"
#: 早期版本生成过的名字，安装/卸载时一并清掉
LEGACY_NAMES=("启动猫伴" "关闭猫伴")
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ "$(uname -s)" != "Darwin" ]; then
  echo "这个脚本只在 macOS 上有意义（需要 osacompile / iconutil / sips）。"
  exit 1
fi

cleanup_legacy() {
  for name in "${LEGACY_NAMES[@]}"; do
    rm -rf "$APPS/$name.app"
    rm -f "$HOME/Desktop/$name" "$HOME/Desktop/$name.app" \
          "$HOME/Desktop/$name.app的替身" "$HOME/Desktop/$name 的替身"
  done
}

if [ "${1:-}" = "--remove" ]; then
  rm -rf "$APP"
  cleanup_legacy
  echo "✅ 已移除 $APP（以及旧版遗留的快捷方式）"
  exit 0
fi

mkdir -p "$APPS"

# ---- ① 图标（自己画，不引用第三方素材）--------------------------------
echo "① 生成图标…"
"$ROOT/.venv/bin/python" "$ROOT/scripts/make_icon.py" "$TMP/icon.png" >/dev/null
ICONSET="$TMP/NekoPal.iconset"
mkdir -p "$ICONSET"
while read -r px name; do
  sips -z "$px" "$px" "$TMP/icon.png" --out "$ICONSET/$name.png" >/dev/null 2>&1
done <<'SIZES'
16 icon_16x16
32 icon_16x16@2x
32 icon_32x32
64 icon_32x32@2x
128 icon_128x128
256 icon_128x128@2x
256 icon_256x256
512 icon_256x256@2x
512 icon_512x512
1024 icon_512x512@2x
SIZES
iconutil -c icns "$ICONSET" -o "$TMP/NekoPal.icns"

# ---- ② 唯一的那个 App -------------------------------------------------
# 用「先写 .applescript 文件再编译」的方式 —— 比 osacompile -e 一层层转义引号清楚得多。
echo "② 生成 NekoPal.app…"
cat > "$TMP/NekoPal.applescript" <<'APPLESCRIPT'
on run
	set root to "__ROOT__"

	-- 已经在跑就别重复开（也避免把正在进行的会话弄乱）
	try
		do shell script "/usr/bin/curl -sf --max-time 1 http://127.0.0.1:8787/api/health > /dev/null"
		display dialog "NekoPal 已经在运行啦～" buttons {"好"} default button 1 with icon note
		return
	end try

	-- 与 scripts/launch.sh 等价的**内联**命令：
	--   · 双击 App 时环境极简，所以自己把 ~/.zshrc 里的 SDKROOT / NEKOPAL_* 捞出来
	--   · 然后交给 CLI 一键启动（变声服务 → 后端 → 原生窗口）
	set shellCmd to "cd " & quoted form of root & " && eval \"$(grep -E '^export (SDKROOT|NEKOPAL_)' \"$HOME/.zshrc\" 2>/dev/null)\" && exec " & quoted form of (root & "/.venv/bin/nekopal") & " up"

	-- 前台运行：窗口开着时 App 显示为「运行中」（Dock 图标 / ⌘Tab 都能找到它），
	-- 关掉窗口它才返回 —— 收尾（后端 + 变声服务）由 nekopal up 自己完成。
	try
		with timeout of 86400 seconds
			do shell script "/bin/bash -c " & quoted form of shellCmd & " >> /tmp/nekopal/launch.log 2>&1"
		end timeout
	on error errMsg
		set tail to ""
		try
			set tail to do shell script "tail -12 /tmp/nekopal/launch.log 2>/dev/null || true"
		end try
		display dialog "启动失败：" & errMsg & return & return & tail ¬
			buttons {"好"} default button 1 with icon caution
	end try
end run
APPLESCRIPT
sed -i '' "s|__ROOT__|$ROOT|" "$TMP/NekoPal.applescript"

rm -rf "$APP"
osacompile -o "$APP" "$TMP/NekoPal.applescript"
cp "$TMP/NekoPal.icns" "$APP/Contents/Resources/applet.icns"
/usr/libexec/PlistBuddy -c "Set :CFBundleName NekoPal" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.productivity" \
  "$APP/Contents/Info.plist" 2>/dev/null || true

# ⚠️ 改过 Info.plist / Resources 之后**必须重新做一次 ad-hoc 签名**。
#    否则签名失效（`codesign -v` 会报 "invalid Info.plist"），
#    macOS 就无法稳定地记住「允许访问文稿文件夹」的授权 → **每次打开都弹窗**（踩过）。
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

echo "③ 清掉旧版本遗留的快捷方式…"
cleanup_legacy

echo
echo "✅ 装好了：~/Applications/NekoPal.app"
echo
echo "   · 打开：聚焦搜索（⌘空格）输入 NekoPal，或直接在 Launchpad 里点"
echo "   · 关闭：**关掉窗口就行** —— 后端与变声服务会一起停"
echo "   · 想放进 Dock：从 Launchpad 把 NekoPal 拖到 Dock 左半边"
echo "   · 卸载：./scripts/install_macos_apps.sh --remove"
echo
echo "ℹ️  第一次打开时，macOS 会问「是否允许访问『文稿』文件夹」——**点允许**。"
echo "    这是因为项目放在 ~/Documents（macOS 的保护目录），不是本项目在偷看你的文件。"
echo "    允许一次后就会记住（前提是别乱删这个 App；重新安装会换签名，可能需要再允许一次）。"
