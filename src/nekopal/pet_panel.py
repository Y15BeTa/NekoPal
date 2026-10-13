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

import sys

__all__ = ["available", "run"]


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
) -> int:
    """起一个 ``NSPanel`` 装着的置顶小条，阻塞到它被关掉。

    返回进程退出码（0 = 正常）。

    :param url: 要加载的页面（后端的 ``/pet.html``）
    :param corner: 初始停在屏幕哪个角，见 ``_CORNERS``
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
    from Foundation import NSURL, NSObject, NSURLRequest
    from WebKit import WKWebsiteDataStore, WKWebView, WKWebViewConfiguration

    # ⚠️ 不把 `WKScriptMessageHandler` 放进基类：pyobjc 下
    #    `class X(NSObject, protocolNamed(...))` 会 **metaclass conflict** 崩掉。
    #    直接继承 NSObject 就行 —— ObjC 只要求"能响应那个 selector"，不看协议声明。
    class _MessageBridge(NSObject):
        """网页 → 原生的消息通道（拖动 / 关闭）。

        ``NSObject`` 必须持有它（``addScriptMessageHandler_name_`` 只存弱引用，
        否则会被 GC 掉，消息就永远收不到）。
        """

        def initWithPanel_(self, panel):  # noqa: N802 - ObjC 选择器命名
            self = objc.super(_MessageBridge, self).init()
            if self is None:
                return None
            self._panel = panel
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

    panel.orderFrontRegardless()  # 显示，但**不激活**（不抢焦点）
    view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
    print(
        "置顶小条：原生 NSPanel 已启用（能浮在全屏之上 + 所有空间可见）",
        flush=True,
    )

    app.run()
    return 0
