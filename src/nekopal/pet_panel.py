"""macOS 原生「置顶小条」外壳：``NSPanel`` + ``WKWebView``。

## 为什么不用 pywebview

pywebview 的窗口**浮不到「别的 App」的全屏之上**（实测 + 官方文档都确认）。
根因在 macOS 的窗口架构：

- 全屏 App 会**独占一个新的「空间」（Space）**，普通窗口只属于某一个 Space；
- ``NSWindowCollectionBehaviorFullScreenAuxiliary`` **只对「自己 App」的全屏窗口生效**
  —— Electron issue `#19674` 原话：
  *"only allows you to overlay atop windows set with `NSWindowCollectionBehaviorFullScreenPrimary`
  **of your own app**"*。
  所以那条路本来就是死的，不只是"我们实现不了"。

**正解是 ``NSPanel``**（``NSWindowStyleMaskNonactivatingPanel``）——
macOS 给这种面板的特权就是「**浮在全屏之上** + **出现在所有 Space**」。三个独立项目都验证了：

- ``ayangweb/BongoCat``（24k★，Rust + Tauri）→ ADR-0003：macOS 侧改用 AppKit ``NSPanel`` + Metal
- ``iuin8/open-pet-agent``（Swift）→ 桌宠 ``NSPanel`` + 跨 Space 自动跟随
- Electron 的 ``{ type: 'panel' }`` → 底层就是加这个 style mask

## 我们只做「窗口外壳」

内容还是后端 serve 的 ``pet.html``，**前端几乎不用改**（只有拖拽和 ✕ 需要跟原生通信，
见下面的 JS bridge）。好处：pyobjc 是 pywebview 的依赖，
**不用装 Node / Rust / 任何额外工具链**。

## JS bridge

网页里会拿到 ``window.nekopalNative``：

.. code-block:: js

    nekopalNative.move(dx, dy)   // 拖动窗口（dx/dy 是屏幕坐标增量）
    nekopalNative.quit()         // 关掉小条

（pywebview 外壳下没有这个对象 —— ``pet.js`` 会自己判断。）
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

__all__ = ["available", "run"]


def window_relative_point(cursor, frame, screen_height) -> tuple:
    """把「鼠标的屏幕坐标」换算成「窗口内坐标」（原点**左上**，与网页一致）。

    ⚠️ 这里是全项目**最容易算错**的一处，所以单独抽成纯函数并配单测：

    - ``CGEventGetLocation`` 给的鼠标位置：原点在**左上**（y 往下增）
    - ``panel.frame()`` 给的窗口位置：原点在**左下**（y 往上增）

    两者**直接相减**会差**一整个屏高** → 判定"鼠标从来不在角色身上" →
    窗口全程点穿 → **整个窗口点不动、像死机** ✗（真踩过，用户报的）。

    正解：先把鼠标 y 翻成 AppKit 口径（``screen_height - cursor_y``），
    再用窗口**顶边**（``win_y + win_h``）减它。

    :param cursor: ``(x, y)``，原点左上
    :param frame: ``(x, y, w, h)``，原点左下（``panel.frame()`` 的口径）
    :param screen_height: 主屏高度（AppKit 点）
    :return: ``(rel_x, rel_y)``，原点左上（和网页 ``getBoundingClientRect`` 一致）
    """

    cursor_x, cursor_y = cursor
    win_x, win_y, _win_w, win_h = frame
    return (
        cursor_x - win_x,
        (win_y + win_h) - (screen_height - cursor_y),
    )


def is_over_any(rects, point, slack: float = 6.0) -> bool:
    """点是否落在任意一个矩形里（都用「窗口内坐标、原点左上」）。

    ``slack`` 是边缘容差：鼠标贴着边界时判定会来回抖，留几像素就不抖了。
    """

    px, py = point
    return any(
        (r["x"] - slack) <= px <= (r["x"] + r["w"] + slack)
        and (r["y"] - slack) <= py <= (r["y"] + r["h"] + slack)
        for r in rects
    )


def _load_origin(state_path: Optional[str]) -> Optional[tuple]:
    """读回上次拖到的窗口位置与尺寸；没有 / 坏了都返回 None（用默认 ✗）。

    为什么要有它：桌宠被你拖到顺手的位置、调到顺眼的大小后，
    重启又弹回角落会很烦 ✗。
    """

    if not state_path:
        return None

    try:
        with open(state_path, encoding="utf-8") as fh:
            data = json.load(fh)
        w = data.get("w")
        h = data.get("h")
        return (float(data["x"]), float(data["y"]), float(w) if w else None,
                float(h) if h else None)
    except Exception:  # noqa: BLE001 - 文件不存在 / 损坏都无所谓，不算错误
        return None


def _save_origin(state_path: Optional[str], frame) -> None:
    """把窗口位置**和尺寸**记下来（失败就算了，不值得打扰用户）。"""

    if not state_path:
        return

    try:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "x": round(frame.origin.x),
                    "y": round(frame.origin.y),
                    "w": round(frame.size.width),
                    "h": round(frame.size.height),
                },
                fh,
            )
    except Exception:  # noqa: BLE001
        pass


def available() -> bool:
    """当前环境能不能用原生外壳（macOS + pyobjc + WebKit）。"""

    if sys.platform != "darwin":
        return False

    try:
        import AppKit  # noqa: F401
        import WebKit  # noqa: F401
    except ImportError:
        return False

    return True


# 角落 → (水平靠哪边, 垂直靠哪边)
_CORNERS = {
    "top-left": ("left", "top"),
    "top-right": ("right", "top"),
    "bottom-left": ("left", "bottom"),
    "bottom-right": ("right", "bottom"),
}


def run(
    url: str,
    width: int,
    height: int,
    margin: int = 16,
    corner: str = "bottom-right",
    click_through: bool = True,
    state_path: Optional[str] = None,
    cursor_image: Optional[str] = None,
    ui_path: Optional[str] = None,
) -> int:
    """起一个 ``NSPanel`` 装着的置顶窗口，阻塞到它被关掉。

    返回进程退出码（0 = 正常）。

    :param url: 要加载的页面（后端的 ``/character.html`` 或 ``/pet.html``）
    :param corner: 初始停在屏幕哪个角，见 ``_CORNERS``
    :param click_through: **精确命中**。为 True 时，只有网页报过来的
        「我身上」的矩形接收鼠标事件，其余透明区域**点穿**到桌面 ——
        否会一个大透明窗口会挡住你点别的东西 ✗。角色窗口该开，小条不用。
    :param state_path: 记住窗口位置的文件；下次启动回到你拖到的地方
    :param cursor_image: 🐾 猫爪光标的 PNG（64×64，按 32 点显示）。
        见下面 ``_apply_paw_cursor`` 的说明 —— **CSS 的 ``cursor:`` 在这个窗口上不生效**。
    :param ui_path: ``ui.json``（读 ``paw_cursor`` 开关；主窗「设置」里能关）
    """

    import objc
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSColor,
        NSMakePoint,
        NSMakeRect,
        NSPanel,
        NSScreen,
        NSStatusWindowLevel,
        NSViewHeightSizable,
        NSViewWidthSizable,
        NSWindowCollectionBehaviorCanJoinAllSpaces,
        NSWindowCollectionBehaviorFullScreenAuxiliary,
        NSWindowCollectionBehaviorStationary,
        NSWindowStyleMaskBorderless,
        NSWindowStyleMaskNonactivatingPanel,
    )
    from Foundation import NSURL, NSObject, NSTimer, NSURLRequest
    from Quartz import (
        CGEventCreate,
        CGEventGetLocation,
        CGEventSourceButtonState,
        kCGEventSourceStateCombinedSessionState,
        kCGMouseButtonLeft,
        kCGMouseButtonRight,
    )
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    # ⚠️ 不把 `WKScriptMessageHandler` 放进基类：pyobjc 下
    #    `class X(NSObject, protocolNamed(...))` 会 **metaclass conflict** 崩掉。
    #    直接继承 NSObject 就行 —— ObjC 只要求"能响应那个 selector"，不看协议声明。
    class _MessageBridge(NSObject):
        """网页 → 原生的消息通道（拖动 / 关闭 / 精确命中）。

        ``NSObject`` 必须持有它（``addScriptMessageHandler_name_`` 只存弱引用，
        否则会被 GC 掉，消息就永远收不到）。
        """

        def initWithPanel_(self, panel):  # noqa: N802 - ObjC 选择器命名
            self = objc.super(_MessageBridge, self).init()
            if self is None:
                return None
            self._panel = panel
            # 网页报过来的「我身上」矩形（窗口内坐标，原点左上）—— 给 _tick 做精确命中
            self._hot_rects: list = []
            # 窗口被拖动过、位置还没落盘
            self._dirty = False
            # 正在拖拽 —— 这期间**必须暂停"点穿"判定**：
            # 拖动时鼠标很容易瞬间滑出可点区域，判定就会把窗口设成穿透 → 拖拽断掉 ✗
            self._dragging = False
            # 当前是否把窗口设成"点穿"
            self._ignoring: object = None
            # 按钮列现在是显示还是收起（None = 还没定过）
            self._hover_shown: object = None
            # 🛡️ 保险：**至少见过一次"鼠标在身上"之前，绝不开启穿透**
            self._ever_inside = False
            # 右键菜单现在开着吗 / 上一帧鼠标是不是按着的（做边沿检测 ✗）
            self._menu_open = False
            self._was_down = False
            # 鼠标是什么时候离开窗口的（菜单开着时用 —— 见 _tick ✗）
            self._left_at: Optional[float] = None
            return self

        def userContentController_didReceiveScriptMessage_(  # noqa: N802
            self, _controller, message
        ):
            body = message.body()
            # ⚠️ **不要**用 `isinstance(body, dict)` 判断：ObjC 传过来的是 `NSDictionary`，
            #    在 pyobjc 下那个判断会**把所有消息静默吞掉** ✗
            #    （踩过：页面握手永远收不到，拖拽和 ✕ 跟着一起失灵，还不报错）。
            getter = getattr(body, "get", None)
            if getter is None:
                print("置顶小条：收到看不懂的消息：{!r}".format(body), flush=True)
                return

            action = getter("action")

            if action == "move":
                # JS 的 y 轴向下，AppKit 向上 —— 所以 dy 要取反
                frame = self._panel.frame()
                self._panel.setFrameOrigin_(
                    NSMakePoint(
                        frame.origin.x + float(body.get("dx", 0)),
                        frame.origin.y - float(body.get("dy", 0)),
                    )
                )
                self._dirty = True  # 让 _tick 稍后把新位置落盘
            elif action == "resize":
                # 窗口缩放（右键菜单里的放大/缩小 ✗）
                # 以**左下角**为锚点，所以窗口是往上/往右长 ✗ —— 贴右下角的桌宠
                # 这个方向最自然（不会跑出屏幕 ✗）
                frame = self._panel.frame()
                width = float(body.get("w", frame.size.width))
                height = float(body.get("h", frame.size.height))
                if width >= 80 and height >= 80:
                    self._panel.setFrame_display_(
                        NSMakeRect(frame.origin.x, frame.origin.y, width, height),
                        True,
                    )
                    self._dirty = True
                    print(
                        "置顶小条：窗口缩放到 {}×{}".format(int(width), int(height)),
                        flush=True,
                    )
            elif action == "menu":
                # 网页告诉我们右键菜单开了 / 关了 —— 关菜单要靠原生 ✗
                self._menu_open = bool(getter("open"))
                print(
                    "置顶小条：右键菜单{}".format(
                        "已打开（开始盯着窗口外点击）" if self._menu_open else "已关闭"
                    ),
                    flush=True,
                )
            elif action == "dragging":
                self._dragging = bool(getter("on"))
            elif action == "hot":
                # 网页告诉我们"哪些矩形算我身上" → 据此做精确命中（见 _tick）
                try:
                    self._hot_rects = [
                        {
                            "x": float(r.get("x", 0)),
                            "y": float(r.get("y", 0)),
                            "w": float(r.get("w", 0)),
                            "h": float(r.get("h", 0)),
                        }
                        for r in (getter("rects") or [])
                    ]
                except Exception as exc:  # noqa: BLE001
                    print("置顶小条：hot 矩形解析失败：{}".format(exc), flush=True)
                else:
                    print(
                        "置顶小条：收到 {} 个「可点区域」→ 其余地方点穿桌面 ✨".format(
                            len(self._hot_rects)
                        ),
                        flush=True,
                    )
            elif action == "hover":
                # 网页的回执：证明 __nekopalHover 真的跑到了
                print(
                    "置顶小条：按钮列{}（网页确认）".format(
                        "已显示" if getter("on") else "已收起"
                    ),
                    flush=True,
                )
            elif action == "log":
                # 网页把消息丢进来 —— 角色窗口没有开发者工具，这是它唯一能"说话"的渠道
                print("网页：{}".format(getter("text")), flush=True)
            elif action == "hello":
                # 页面加载完的第一声 —— 日志里有它，就证明网页**真的渲染出来了**
                print("置顶小条：网页已加载 ✅（原生外壳里正常渲染）", flush=True)
            elif action == "quit":
                self._panel.orderOut_(None)
                NSApplication.sharedApplication().terminate_(None)

    class _NavDelegate(NSObject):
        """把「页面加载成功 / 失败」打进日志。

        没有它的话，加载失败是**完全静默**的 —— 你只会看到一个空窗口，
        日志里一句解释都没有（踩过，所以补上）。
        """

        def webView_didFinishNavigation_(self, _view, _nav):  # noqa: N802
            print("置顶小条：页面加载完成 ✅", flush=True)

        def webView_didFailNavigation_withError_(self, _view, _nav, error):  # noqa: N802
            print("置顶小条：页面加载失败 ✗ {}".format(error), flush=True)

        def webView_didFailProvisionalNavigation_withError_(  # noqa: N802
            self, _view, _nav, error
        ):
            print("置顶小条：连不上页面 ✗ {}".format(error), flush=True)

    app = NSApplication.sharedApplication()
    # 不占 Dock、不抢焦点 —— 桌宠本来就该"存在但不要你管"
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    # 摆位：默认右下角，且避开菜单栏 / Dock（visibleFrame 已经扣掉了它们）
    side_h, side_v = _CORNERS.get(corner, _CORNERS["bottom-right"])
    frame = NSScreen.mainScreen().visibleFrame()
    if side_h == "right":
        x = frame.origin.x + frame.size.width - width - margin
    else:
        x = frame.origin.x + margin
    if side_v == "bottom":
        y = frame.origin.y + margin
    else:
        y = frame.origin.y + frame.size.height - height - margin

    # ⚠️ NSWindowStyleMaskNonactivatingPanel 是关键：**点它不会抢走你当前 App 的焦点**，
    #    也正是它让 macOS 允许窗口浮在别人的全屏之上。
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(x, y, width, height),
        NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
        NSBackingStoreBuffered,
        False,
    )
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(False)
    panel.setFloatingPanel_(True)
    # ⚠️ 顺序很重要：`setFloatingPanel_` 会把 level 改成 floating(3)，
    #    所以 `setLevel_` 必须写在它**后面**，否则被悄悄覆盖（实测 layer=3 而非 25）。
    panel.setLevel_(NSStatusWindowLevel)  # 比普通窗口高（也在 Dock 之上）
    panel.setBecomesKeyOnlyIfNeeded_(True)
    # ⚠️ NSPanel 默认「App 失焦就自动隐藏」—— 不关掉的话小条一切窗口就没了 ✗
    panel.setHidesOnDeactivate_(False)
    # 🎯 这就是"跨全屏"的全部秘密：**加入所有空间** + **全屏辅助**
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
    )
    panel.setMovableByWindowBackground_(True)  # 卡片留白处也能拖（网页区域走 JS bridge）
    # 让窗口即使在**不是 key window** 时也收得到 mouseMoved ——
    # 否则网页里的 hover / 拖拽会时灵时不灵（我们是不抢焦点的 accessory app）。
    panel.setAcceptsMouseMovedEvents_(True)

    config = WKWebViewConfiguration.alloc().init()
    # ⚠️ 用**非持久**数据存储：否则 WKWebView 会把 pet.js 缓存到磁盘，
    #    升级 nekopal 之后小条可能还在跑**旧 JS**（踩过：改了 pet.js，窗口行为却没变）。
    #    我们不需要 cookie / localStorage，所以代价为零 —— 换"永远拿最新代码"。
    config.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
    controller = config.userContentController()
    bridge = _MessageBridge.alloc().initWithPanel_(panel)
    controller.addScriptMessageHandler_name_(bridge, "nekopal")

    view = WKWebView.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, width, height), config
    )
    # ⚠️ 委托是**弱引用**，必须留一个 Python 引用，否则被 GC 掉 → 日志又变哑巴
    nav_delegate = _NavDelegate.alloc().init()
    view.setNavigationDelegate_(nav_delegate)
    view.setOpaque_(False)
    view.setBackgroundColor_(NSColor.clearColor())
    # WKWebView 的透明背景：`drawsBackground` 是私有但被广泛使用的 key
    # （pywebview 走的就是同一条路，会有一条 deprecation 警告，无害）
    try:
        view.setValue_forKey_(False, "drawsBackground")
    except Exception:  # noqa: BLE001 - 未来版本若禁掉，就退回不透明（功能不受影响）
        pass
    view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    panel.setContentView_(view)

    # 位置与尺寸：优先回到你上次调好的样子（不然每次启动都被拽回角落 ✗）
    saved = _load_origin(state_path)
    if saved is not None:
        x, y, w, h = saved
        if w and h:
            panel.setFrame_display_(NSMakeRect(x, y, w, h), False)
        else:
            panel.setFrameOrigin_(NSMakePoint(x, y))

    # ── 精确命中 + 记住位置 ──────────────────────────────────────────────
    # 为什么需要精确命中：窗口是个**矩形**，但角色不是 ✗ ——
    # 不做这个的话，一个大的透明窗口会**挡住你点桌面上的东西** ✗。
    # 做法：每 80ms 看一眼鼠标在哪，只有落在网页报过来的「我身上」的矩形里
    # 才接收鼠标事件，否则 setIgnoresMouseEvents_(True) → 点穿到桌面 ✅。
    #
    # 读鼠标位置用 CGEventCreate(None) —— **不需要任何系统授权**（实测 ✅）。
    saved_at = [0.0]
    debug_once = [True]  # 第一次命中判定时，把关键数字打出来一次（排查坐标问题用）
    ticks = [0]
    warned = [False]
    diag_at = [0]  # 「菜单开着但关不掉」时的节流诊断 ✗

    def _report_js_error(_result, error) -> None:
        """evaluateJavaScript 的回调：只在**出错**时说话（平时闭嘴）。"""
        if error is not None:
            print("置顶小条：通知网页时出错：{}".format(error), flush=True)

    def _call_js(expression: str) -> None:
        """在网页里跑一句 JS（失败就算了 ✗）。"""
        try:
            view.evaluateJavaScript_completionHandler_(expression, _report_js_error)
        except Exception as exc:  # noqa: BLE001
            print("置顶小条：通知网页失败：{}".format(exc), flush=True)

    def _tell_page_hover(on: bool) -> None:
        """告诉网页「鼠标还在我身上吗」—— 用来显示 / 收起按钮列。

        ⚠️ 为什么由**原生**来驱动，而不是让网页自己用 CSS ``:hover``：
        鼠标一离开可点区域，我们就把窗口设成"点穿"，网页**收不到 mouseleave**，
        ``:hover`` 会卡住 → 按钮永远不消失 ✗（用户实测踩到）。
        只有原生才知道鼠标到底还在不在角色身上，所以由它说了算。
        """
        _call_js(
            "window.__nekopalHover && window.__nekopalHover({})".format(
                "true" if on else "false"
            )
        )

    # 光标位置 → 网页（给「视线跟随 / 猫爪光标」用）
    # 为什么也要原生推：透明区域是"点穿"的，网页在**那些地方收不到 mousemove** ——
    # 而"看向光标"恰恰最需要知道光标在窗口边缘 / 外面时的位置。
    # 反正这个 tick 本来每 80ms 就在读光标（命中判定要用），顺手推一下不额外花钱。
    last_cursor = [None]

    def _tell_page_cursor(x, y):
        prev = last_cursor[0]
        # 没怎么动就别刷（省一次跨语言调用）
        if prev is not None and abs(prev[0] - x) < 4 and abs(prev[1] - y) < 4:
            return
        last_cursor[0] = (x, y)
        _call_js(
            "window.__nekopalCursor && window.__nekopalCursor({:.0f}, {:.0f})".format(x, y)
        )

    # 🐾 猫爪光标
    # 为什么**不用 CSS** `cursor:`（网页里明明是对的）——
    # 实测在 NSPanel 上**完全不生效**：连 `grab` 手型都不显示，
    # 因为这是个**非激活**的 accessory app 面板，网页设的光标到不了系统。
    # 只能由原生 `NSCursor.set()` 来设。反正这个 tick 每 80ms 就在判
    # 「鼠标在不在身上」，顺手切光标不额外花钱。
    paw = {
        "cursor": None, "ok": None, "wanted": None, "checked": -9.0,
        "rects": None, "tracked": False, "delegate": None,
    }

    def _make_paw_owner():
        """建那个 cursorUpdate 的接收者（要**留引用**，否则会被 GC 掉 ✗）。"""

        return _PawCursorOwner.alloc().init()

    def _paw_wanted() -> bool:
        """要不要猫爪（每 ~2 秒读一次 ui.json，别每帧读文件）。"""
        import json as _json
        import time as _time

        now = _time.monotonic()
        if paw["wanted"] is not None and now - paw["checked"] < 2.0:
            return paw["wanted"]
        paw["checked"] = now
        try:
            data = _json.loads(Path(ui_path).read_text(encoding="utf-8")) if ui_path else {}
            paw["wanted"] = data.get("paw_cursor") is not False
        except Exception:  # noqa: BLE001 - 读不到就当"要"（默认开）
            paw["wanted"] = True
        return paw["wanted"]

    def _apply_paw_cursor(inside: bool) -> None:
        try:
            from AppKit import NSCursor, NSImage, NSMakeSize

            if paw["cursor"] is None and cursor_image and Path(cursor_image).is_file():
                img = NSImage.alloc().initWithContentsOfFile_(str(cursor_image))
                if img is not None:
                    print(
                        "置顶小条：猫爪图 {}×{} 合法={} reps={}".format(
                            img.size().width, img.size().height, img.isValid(),
                            img.representations().__len__(),
                        ),
                        flush=True,
                    )
                    # 图是 64px，按 **32 点**显示 —— 高分屏才锐利
                    img.setSize_(NSMakeSize(32.0, 32.0))
                    # 热点 (16,26)：图的上方中间（AppKit 原点在**左下**，32-6=26）
                    paw["cursor"] = NSCursor.alloc().initWithImage_hotSpot_(
                        img, NSMakePoint(16.0, 26.0)
                    )
                    print("置顶小条：猫爪光标已加载 ✅", flush=True)

            enabled = _paw_wanted()
            # 热区变了（按钮浮现 / 菜单收起）→ 重新登记光标区域
            # 给 owner 用：它靠这个判断"现在在不在她身上"
            paw["inside"] = bool(inside)
            # 跟踪区域只装一次（光标加载好之后）
            if enabled and paw["cursor"] is not None:
                _install_paw_tracking()
            want = inside and enabled and paw["cursor"] is not None
            if want:
                # 每次 tick 都重申一遍 —— 别的程序会顺手把光标抢走
                paw["cursor"].set()
            elif paw["ok"]:
                # 只在"离开"那一次还原成箭头（离开就点穿了，不该再管桌面光标）
                NSCursor.arrowCursor().set()
            if want != paw["ok"]:
                # 状态翻转时说清楚**每个条件**分别是真是假 —— 好定位卡在哪一步
                print(
                    "置顶小条：猫爪光标 {}（在可点区域={} · 开关={} · 已加载={}）".format(
                        "开" if want else "关", inside, enabled, paw["cursor"] is not None
                    ),
                    flush=True,
                )
            paw["ok"] = want
        except Exception as exc:  # noqa: BLE001 - 光标失败绝不能让窗口炸掉
            if paw["ok"] is None:
                paw["ok"] = False
                print("置顶小条：猫爪光标不可用（{}）—— 用系统箭头".format(exc), flush=True)

    class _PawCursorOwner(NSObject):
        """光标在「她身上」时把它换成猫爪。

        ⚠️ 为什么非要走 AppKit 这套钩子 ✗：
        2026-10-14 实测 —— `NSCursor.set()`（每 80ms 一次）和
        `addCursorRect:forCursor:` 在这个**非激活的 accessory 面板**上**都不生效**：
        日志证明条件全对、光标也加载成功，屏幕上却始终是箭头。
        `NSTrackingActiveAlways` 是 AppKit 专门为「App 不是前台也要改光标」提供的开关。

        ⚠️ 为什么**整窗一个区域**而不是"每块热区一个" ✗：
        逐矩形登记要靠 `removeAllTrackingAreas`（pyobjc 没暴露 ✗）来清理，
        而且热区会随按钮浮现 / 菜单开合变化 ✗。
        改成整窗一个区域 + **每次鼠标移动都判一次** —— 反正 `inside`
        原生每 80ms 已经算好了，直接拿来用 ✅
        """

        def _apply(self):
            try:
                from AppKit import NSCursor

                want_paw = bool(
                    paw.get("inside") and paw["cursor"] is not None and _paw_wanted()
                )
                was = paw.get("applied")
                if want_paw:
                    paw["cursor"].set()
                elif was:
                    # ⚠️ **只在"刚才还是猫爪"那一次还原** ✗
                    #    每帧都设箭头的话，会把按钮上的 CSS 光标（小手）也一起覆盖掉 ✗
                    NSCursor.arrowCursor().set()
                if want_paw != was:
                    paw["applied"] = want_paw
                    print(
                        "置顶小条：cursorUpdate → 设成 {}".format("猫爪 🐾" if want_paw else "箭头"),
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001
                if not paw.get("apply_err"):
                    paw["apply_err"] = True
                    print("置顶小条：设光标出错（{}）".format(exc), flush=True)

        def cursorUpdate_(self, _event):  # noqa: N802
            self._apply()

        def mouseMoved_(self, _event):  # noqa: N802
            self._apply()

        def mouseExited_(self, _event):  # noqa: N802
            self._apply()

    # ⚠️ 必须留引用：跟踪区域的 owner 是**弱引用**，被 GC 掉就再也不回调了
    paw["delegate"] = _PawCursorOwner.alloc().init()

    def _install_paw_tracking() -> None:
        """整窗装**一个**跟踪区域，让 `cursorUpdate:` / `mouseMoved:` 能被叫到。

        只装一次；`NSTrackingInVisibleRect` 会让它自动跟着窗口尺寸走。
        """

        if paw["tracked"]:
            return
        try:
            from AppKit import (
                NSMakeRect,
                NSTrackingActiveAlways,
                NSTrackingArea,
                NSTrackingCursorUpdate,
                NSTrackingInVisibleRect,
                NSTrackingMouseEnteredAndExited,
                NSTrackingMouseMoved,
            )

            area = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                NSMakeRect(0.0, 0.0, 1.0, 1.0),
                NSTrackingCursorUpdate
                | NSTrackingMouseMoved
                | NSTrackingMouseEnteredAndExited
                | NSTrackingActiveAlways
                | NSTrackingInVisibleRect,
                paw["delegate"],
                None,
            )
            view.addTrackingArea_(area)
            paw["tracked"] = True
            print("置顶小条：猫爪跟踪区域已装好 ✅", flush=True)
        except Exception as exc:  # noqa: BLE001 - 装不上不能让窗口炸掉
            paw["tracked"] = True  # 别每帧重试刷屏
            print("置顶小条：装猫爪跟踪区域失败（{}）".format(exc), flush=True)

    def _tick(_timer):
        # ⚠️ 计时器里**绝对不能漏异常**：pyobjc 会把 ObjC 回调里的 Python 异常
        #    直接变成进程 abort（连 traceback 都不一定打得出来 ✗）。
        #    我们踩过一次：这里少 import 了 `time`，窗口起来 80ms 后整个进程消失，
        #    日志里只剩一片空白。所以**整个身体都包起来**。
        try:
            ticks[0] += 1
            loc = CGEventGetLocation(CGEventCreate(None))
            frame = panel.frame()

            if click_through:
                rel_x = rel_y = None  # 只在下面算得出来；拖拽/没矩形时保持 None
                if bridge._dragging:
                    inside = True
                else:
                    rects = bridge._hot_rects
                    # 网页还没报到时先当"能点"处理，免得它自己都点不了
                    inside = True
                    if rects:
                        # 坐标换算与命中判断都在**纯函数**里（有单测）——
                        # 这两步以前内联在这儿，算错了也不报错，只是"窗口点不动"✗
                        screen_h = NSScreen.mainScreen().frame().size.height
                        rel_x, rel_y = window_relative_point(
                            (loc.x, loc.y),
                            (
                                frame.origin.x,
                                frame.origin.y,
                                frame.size.width,
                                frame.size.height,
                            ),
                            screen_h,
                        )
                        inside = is_over_any(rects, (rel_x, rel_y))
                        # 顺手把光标位置推给网页 → 视线跟随（见 _tell_page_cursor）
                        _tell_page_cursor(rel_x, rel_y)
                        if debug_once[0]:
                            debug_once[0] = False
                            print(
                                "置顶小条：命中判定自检 —— 鼠标({:.0f},{:.0f}) "
                                "屏高{:.0f} 窗口({:.0f},{:.0f}) {}×{} "
                                "→ 窗口内({:.0f},{:.0f}) 判定={}".format(
                                    loc.x, loc.y, screen_h,
                                    frame.origin.x, frame.origin.y,
                                    frame.size.width, frame.size.height,
                                    rel_x, rel_y,
                                    "在身上" if inside else "在外面",
                                ),
                                flush=True,
                            )
                    else:
                        # 网页还没报到 —— 先什么都别动（默认：能点、按钮不显示）
                        inside = None

                if inside is not None:
                    # 🐾 猫爪光标（放在最前面：切光标要**立刻**生效）
                    _apply_paw_cursor(inside)
                    if inside:
                        bridge._ever_inside = True
                    # 🛡️ 保险：在"见过鼠标进身上"之前**绝不开启穿透**。
                    #    万一以后坐标又算错，最坏只是"透明区域挡鼠标"（能忍），
                    #    而不是"整个窗口点不动、像死机"（不能忍）✗
                    should_ignore = (not inside) and bridge._ever_inside
                    if should_ignore is not bridge._ignoring:
                        bridge._ignoring = should_ignore
                        panel.setIgnoresMouseEvents_(should_ignore)

                    # 显示 / 收起按钮 —— **由原生通知网页**（见 _tell_page_hover）。
                    # ⚠️ 这一步要跟着「在不在身上」走，**不能跟着"穿透"走**：
                    #    保险会让穿透晚一步生效，跟错的话按钮和穿透状态会打架 ✗
                    if inside is not bridge._hover_shown:
                        bridge._hover_shown = inside
                        _tell_page_hover(inside)
                        print(
                            "置顶小条：鼠标{}「可点区域」（窗口内 {:.0f},{:.0f}）→ {}".format(
                                "进入" if inside else "离开",
                                rel_x or 0,
                                rel_y or 0,
                                "正常接收点击 ✅" if inside else "点穿桌面、收起按钮 ✨",
                            ),
                            flush=True,
                        )

                    # 12 秒了还没见过鼠标进身上 → 可能是坐标算错了。提一句，
                    # 但说清楚"如果本来就没移过去，那是正常的"（免得误报吓人）
                    if not bridge._ever_inside and not warned[0] and ticks[0] > 150:
                        warned[0] = True
                        print(
                            "置顶小条：还没见过鼠标进入「可点区域」"
                            "（如果还没把鼠标移到猫身上，这是正常的；"
                            "移过去了却没反应才是坐标算错）",
                            flush=True,
                        )

            # 菜单开着的时候：光标在窗口**外面**按下鼠标 → 让网页关掉菜单 ✗
            # （窗口外面是"点穿"的 ✗，网页收不到那次点击 ✗ —— 只有原生知道 ✗）
            if click_through and bridge._menu_open and inside is False:
                down = bool(
                    CGEventSourceButtonState(
                        kCGEventSourceStateCombinedSessionState, kCGMouseButtonLeft
                    )
                    or CGEventSourceButtonState(
                        kCGEventSourceStateCombinedSessionState, kCGMouseButtonRight
                    )
                )
                # ⚠️ 不做边沿检测 ✗：轮询只有 12.5Hz，**快点一下**（按下+松开都在 80ms 内）
                #    会被漏掉 ✗。所以只要"按着"就通知 ✗；
                #    页面关掉菜单后会回报 menu:false ✗，重复调用自然就停了 ✅
                if down:
                    print("置顶小条：窗口外点击 → 收起右键菜单", flush=True)
                    _call_js(
                        "window.__nekopalCloseMenu && window.__nekopalCloseMenu()"
                    )
                    bridge._left_at = None
                else:
                    # ⚠️ **不能只靠按键** ✗：轮询只有 12.5Hz，**快点一下**
                    #    （按下+松开都落在 80ms 内）会被漏掉 ✗（用户报的正是这个 ✗）。
                    #    所以补一条更可靠的路：**鼠标离开窗口超过 0.6 秒**就收菜单 ✅
                    #    （0.6 秒够你"甩出去又回来" ✗，不至于误关 ✗）
                    now2 = time.monotonic()
                    if bridge._left_at is None:
                        bridge._left_at = now2
                    elif now2 - bridge._left_at > 0.6:
                        print("置顶小条：鼠标离开窗口 → 收起右键菜单", flush=True)
                        _call_js(
                            "window.__nekopalCloseMenu && window.__nekopalCloseMenu()"
                        )
                        bridge._left_at = None
                bridge._was_down = down

                # 节流诊断（每秒一次 ✗）：万一关不掉，这行会说明是哪个条件不对 ✗
                if ticks[0] - diag_at[0] > 12:
                    diag_at[0] = ticks[0]
                    print(
                        "置顶小条：菜单开着 · 判定 在外 · 按键 {} · 窗口内({:.0f},{:.0f})".format(
                            down, rel_x or 0, rel_y or 0
                        ),
                        flush=True,
                    )
            else:
                bridge._was_down = False
                bridge._left_at = None

            # 顺手把窗口位置记下来（拖动后 1 秒落一次盘，别每帧都写文件）
            now = time.monotonic()
            if bridge._dirty and now - saved_at[0] > 1.0:
                saved_at[0] = now
                bridge._dirty = False
                _save_origin(state_path, frame)
        except Exception as exc:  # noqa: BLE001 - 看得见 > 静默消失
            print("置顶小条：内部计时器出错：{}".format(exc), flush=True)

    # 12.5 Hz：够跟手，又几乎不耗电（每次只做一次坐标比较）
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.08, True, _tick)
    panel.orderFrontRegardless()  # 显示，但**不激活**（不抢焦点）
    view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
    print(
        "置顶小条：原生 NSPanel 已启用（能浮在全屏之上 + 所有空间可见）",
        flush=True,
    )

    app.run()
    return 0
