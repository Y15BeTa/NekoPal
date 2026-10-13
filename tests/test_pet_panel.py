"""``pet_panel`` 里那几个**纯函数**的单测（不依赖 macOS、不开窗）。

为什么单独把它们拎出来测：坐标换算是这个项目**最容易错、而且错了不报错**的地方 ——
坏了不会崩，只会变成"窗口点不动"，看起来像死机 ✗（已经真踩过一次）。
抽成纯函数 + 单测，才是对那类 bug 的真正修复。
"""

from nekopal.pet_panel import is_over_any, window_relative_point

# 一块典型的 320×440 角色窗口，贴在**屏幕底部**右下角、偏移 16：
#   AppKit 口径（原点左下）：x=1120, y=16, 320×440 → 纵向占 16..456
#   主屏高 956
WIN = (1120, 16, 320, 440)
SCREEN_H = 956

# 网页报过来的两个「可点区域」（窗口内坐标、原点**左上**）
HOT = [
    {"x": 86, "y": 10, "w": 224, "h": 420},  # 角色本体
    {"x": 12, "y": 120, "w": 36, "h": 200},  # 按钮列
]


def test_屏幕上半部分的鼠标算在窗口外():
    """⬅️ 当初的翻车现场（回归测试）。

    鼠标在屏幕靠上处（CG y=100，从上往下量）→ 窗口贴在底部 → 必须是**外面**。

    当年少了 ``screen_height`` 那一项，把两个不同原点的坐标系直接相减：
    ``456 - 100 = 356``，落在 0..440 之内 → 判成"在里面"，
    于是穿透开关全程反着来 → 整个窗口**点不动，像死机** ✗
    """

    rel = window_relative_point((500, 100), WIN, SCREEN_H)

    assert rel == (-620, -400)  # x: 500-1120；y: (16+440)-(956-100)
    assert not is_over_any(HOT, rel)


def test_窗口里的点算在里面():
    """鼠标在屏幕底部附近（CG y=900）→ 应落进窗口，并且命中角色本体。"""

    rel = window_relative_point((1120 + 200, 900), WIN, SCREEN_H)

    assert rel == (200, 400)  # y: (16+440) - (956-900) = 400
    assert is_over_any(HOT, rel)


def test_窗口顶边贴着屏幕上沿时的镜像关系():
    """窗口顶边正好等于屏高时，rel_y 应当和鼠标的 CG y 相等。"""

    frame = (800, 956 - 440, 320, 440)  # 顶边 = 956
    rel = window_relative_point((1000, 220), frame, SCREEN_H)

    assert rel == (200, 220)
    assert is_over_any(HOT, rel)


def test_没有矩形时谁都不算命中():
    assert not is_over_any([], (200, 220))


def test_边缘容差():
    """贴着边界时留几像素容差，免得判定来回抖。"""

    assert not is_over_any(HOT, (60, 220))  # 离角色左边(86)差 26px → 不算
    assert is_over_any(HOT, (83, 220))  # 差 3px，在 6px 容差内 → 算
    assert is_over_any(HOT, (316, 430))  # 角色右下角边界上
    assert not is_over_any(HOT, (20, 400))  # 左边空白（按钮列也没那么长）


def test_容差可以收紧():
    rects = [{"x": 100, "y": 100, "w": 50, "h": 50}]

    assert is_over_any(rects, (95, 120), slack=6.0)
    assert not is_over_any(rects, (95, 120), slack=0.0)
