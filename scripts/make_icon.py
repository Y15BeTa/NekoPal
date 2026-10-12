#!/usr/bin/env python3
"""生成 NekoPal 的应用图标 —— **纯标准库**，不依赖 Pillow。

自己画一只极简猫脸（圆角方底 + 耳朵 + 头 + 眼 + 鼻子），
**不引用任何第三方素材** —— 与项目「仓库零素材」的原则一致。

    python3 scripts/make_icon.py out.png              # 1024×1024
    python3 scripts/make_icon.py --size 256 out.png   # 任意边长

生成的 PNG 由 `scripts/install_macos_apps.sh` 交给 `sips` + `iconutil` 转成 .icns。
"""

from __future__ import annotations

import argparse
import math
import struct
import zlib
from typing import Tuple

BG_TOP = (255, 154, 102)      # 暖橙
BG_BOTTOM = (255, 106, 138)   # 玫红（竖向渐变）
CAT = (255, 250, 245)         # 奶白
EYE = (61, 47, 40)            # 深棕
NOSE = (255, 143, 143)

SS = 3  # 每像素超采样倍数（抗锯齿）
CORNER = 0.235
HEAD = (0.5, 0.605, 0.275)
EYES = ((0.418, 0.568, 0.046), (0.582, 0.568, 0.046))
EAR_L = ((0.262, 0.470), (0.330, 0.208), (0.482, 0.372))
EAR_R = ((0.738, 0.470), (0.670, 0.208), (0.518, 0.372))
NOSE_TRI = ((0.500, 0.718), (0.460, 0.668), (0.540, 0.668))


def _rounded_rect(x: float, y: float, radius: float) -> bool:
    dx = max(radius - x, x - (1.0 - radius), 0.0)
    dy = max(radius - y, y - (1.0 - radius), 0.0)
    return math.hypot(dx, dy) <= radius


def _triangle(x: float, y: float, a, b, c) -> bool:
    def cross(p, q, r):
        return (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])

    d1, d2, d3 = cross((x, y), a, b), cross((x, y), b, c), cross((x, y), c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def _circle(x: float, y: float, cx: float, cy: float, r: float) -> bool:
    return math.hypot(x - cx, y - cy) <= r


def sample(x: float, y: float) -> Tuple[float, float, float, float]:
    """返回该点的 RGBA（各分量 0..1）。

    ⚠️ **判断顺序要紧**：先画「内部的」细节（眼睛 / 鼻子），再画头 ——
    否则头会先命中并把它们整个盖掉（第一次就是这么画出个大白球的）。
    """

    if not _rounded_rect(x, y, CORNER):
        return (0.0, 0.0, 0.0, 0.0)

    for cx, cy, r in EYES:
        if _circle(x, y, cx, cy, r):
            return (*_rgb(EYE), 1.0)
    if _triangle(x, y, *NOSE_TRI):
        return (*_rgb(NOSE), 1.0)

    # 耳朵与头连成一片白色
    if _triangle(x, y, *EAR_L) or _triangle(x, y, *EAR_R) or _circle(x, y, *HEAD):
        return (*_rgb(CAT), 1.0)

    bg = tuple((BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * y) / 255.0 for i in range(3))
    return (*bg, 1.0)


def _rgb(triple) -> Tuple[float, float, float]:
    return tuple(c / 255.0 for c in triple)


def render(size: int) -> bytes:
    """渲染成 PNG 的原始扫描行（每行前面带 filter 字节 0）。"""

    out = bytearray()
    step = 1.0 / size
    for py in range(size):
        out.append(0)  # PNG filter: None
        y0 = py * step
        for px in range(size):
            x0 = px * step
            r = g = b = a = 0.0
            for sy in range(SS):
                yy = y0 + (sy + 0.5) * step / SS
                for sx in range(SS):
                    xx = x0 + (sx + 0.5) * step / SS
                    sr, sg, sb, sa = sample(xx, yy)
                    r += sr * sa
                    g += sg * sa
                    b += sb * sa
                    a += sa
            n = SS * SS
            if a > 0:
                out += bytes(
                    (
                        int(round(r / a * 255)),
                        int(round(g / a * 255)),
                        int(round(b / a * 255)),
                        int(round(a / n * 255)),
                    )
                )
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def write_png(path: str, size: int, raw: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as handle:
        handle.write(png)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 NekoPal 图标（纯标准库）")
    parser.add_argument("output", help="输出的 PNG 路径")
    parser.add_argument("--size", type=int, default=1024, help="边长像素（默认 1024）")
    args = parser.parse_args()

    write_png(args.output, args.size, render(args.size))
    print("✅ 已生成 {}（{}×{}）".format(args.output, args.size, args.size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
