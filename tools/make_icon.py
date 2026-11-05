"""生成多尺寸应用图标（与 docs/icon/app-icon.svg 同一套几何关系）。

按目标尺寸分别用 4x 超采样矢量绘制后缩放，而非从一张大图拉伸：
- assets/app.ico 内含 16/24/32/48/64/128/256
- docs/icon/app-icon-512.png 作为预览
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICO = os.path.join(ROOT, "assets", "app.ico")
PNG = os.path.join(ROOT, "docs", "icon", "app-icon-512.png")
SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 8  # 超采样倍数


def _mix(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def draw(size: int) -> Image.Image:
    canvas = size * SS
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = canvas / 256.0

    def px(v: float) -> float:
        return v * u

    # 底板（垂直渐变用逐行矩形模拟）
    top, bottom = (244, 251, 254), (214, 235, 243)
    for y in range(int(px(8)), int(px(248))):
        t = (y - px(8)) / max(1.0, px(240))
        d.line([(px(8), y), (px(248), y)], fill=_mix(top, bottom, t) + (255,))
    # 圆角遮罩
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).rounded_rectangle([px(8), px(8), px(248), px(248)], radius=px(48), fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([px(8), px(8), px(248), px(248)], radius=px(48), outline=(196, 223, 233, 255), width=max(1, int(px(2))))

    # 文件主体（左上到右上折角）
    body = (46, 54, 168, 202, 14)
    d.rounded_rectangle([px(46), px(54), px(168), px(202)], radius=px(14), fill=(255, 255, 255, 255),
                        outline=(169, 207, 220, 255), width=max(1, int(px(3))))
    d.polygon([(px(138), px(54)), (px(168), px(84)), (px(146), px(84)), (px(138), px(76))],
              fill=(227, 241, 246, 255), outline=None)
    d.line([(px(138), px(54)), (px(168), px(84))], fill=(169, 207, 220, 255), width=max(1, int(px(3))))

    # 文本行（小尺寸下省略，保持轮廓干净）
    if size >= 32:
        for y, w in ((104, 72), (126, 88), (148, 56)):
            d.rounded_rectangle([px(64), px(y), px(64 + w), px(y + 9)], radius=px(4.5), fill=(203, 225, 234, 255))

    # 时钟（白色描边环把时钟与文件分开，小尺寸也清晰）
    cx, cy = 176, 176
    d.ellipse([px(cx - 52), px(cy - 52), px(cx + 52), px(cy + 52)], fill=(255, 255, 255, 255))
    d.ellipse([px(cx - 44), px(cy - 44), px(cx + 44), px(cy + 44)], fill=(47, 159, 181, 255))
    lw = max(2, int(px(8)))
    d.line([(px(cx), px(cy - 28)), (px(cx), px(cy))], fill=(255, 255, 255, 255), width=lw)
    d.line([(px(cx), px(cy)), (px(cx + 26), px(cy))], fill=(255, 255, 255, 255), width=lw)
    d.ellipse([px(cx - 5), px(cy - 5), px(cx + 5), px(cy + 5)], fill=(255, 255, 255, 255))

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    os.makedirs(os.path.dirname(ICO), exist_ok=True)
    os.makedirs(os.path.dirname(PNG), exist_ok=True)
    frames = [draw(s) for s in SIZES]
    frames[-1].save(ICO, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[:-1])
    draw(512).save(PNG, format="PNG")
    print("ico:", ICO, os.path.getsize(ICO))
    print("png:", PNG, os.path.getsize(PNG))
    # 逐尺寸自检：确认 ico 内确实包含各尺寸
    with Image.open(ICO) as im:
        print("ico sizes:", sorted(im.ico.sizes()))


if __name__ == "__main__":
    sys.exit(main())
