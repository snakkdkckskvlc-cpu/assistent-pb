"""Собирает PNG-иконки разных размеров, .icns (macOS) и .ico (Windows).

Требует: Pillow, cairosvg (или использует ImageMagick).
На macOS для .icns использует системный `iconutil`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BUILD = ROOT / "build" / "icons"
BUILD.mkdir(parents=True, exist_ok=True)

SVG = FRONTEND / "icon.svg"
SIZES = [16, 32, 64, 128, 256, 512, 1024]


def rasterize_with_cairosvg(size: int, out: Path) -> None:
    import cairosvg

    cairosvg.svg2png(url=str(SVG), write_to=str(out), output_width=size, output_height=size)


def rasterize_with_pil(size: int, out: Path) -> None:
    """Fallback: рендерим SVG-фигуру напрямую через PIL (без cairo)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size
    # Тёмный фон со скруглением
    d.rounded_rectangle([0, 0, r, r], radius=int(r * 0.22), fill=(26, 29, 35, 255))
    # Языки пламени — три эллипса поверх
    cx, cy = r // 2, int(r * 0.55)
    d.ellipse([cx - r * 0.32, cy - r * 0.20, cx + r * 0.32, cy + r * 0.28], fill=(200, 53, 43, 255))
    d.ellipse(
        [cx - r * 0.22, cy - r * 0.12, cx + r * 0.22, cy + r * 0.24], fill=(242, 140, 26, 255)
    )
    d.ellipse(
        [cx - r * 0.10, cy - r * 0.02, cx + r * 0.10, cy + r * 0.18], fill=(255, 215, 94, 255)
    )
    img.save(out, "PNG")


def rasterize(size: int, out: Path) -> None:
    try:
        rasterize_with_cairosvg(size, out)
    except Exception:
        rasterize_with_pil(size, out)


def build_icns() -> Path | None:
    """macOS: собирает .icns из PNG-набора через iconutil."""
    iconset = BUILD / "AppIcon.iconset"
    iconset.mkdir(exist_ok=True)
    mapping = {
        16: "icon_16x16.png",
        32: ("icon_16x16@2x.png", "icon_32x32.png"),
        64: "icon_32x32@2x.png",
        128: "icon_128x128.png",
        256: ("icon_128x128@2x.png", "icon_256x256.png"),
        512: ("icon_256x256@2x.png", "icon_512x512.png"),
        1024: "icon_512x512@2x.png",
    }
    for size, names in mapping.items():
        src = BUILD / f"icon_{size}.png"
        if not src.exists():
            continue
        if isinstance(names, str):
            names = (names,)
        for n in names:
            (iconset / n).write_bytes(src.read_bytes())
    icns = BUILD / "AppIcon.icns"
    try:
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
        return icns
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"iconutil failed ({e}); .icns не собран")
        return None


def build_ico() -> Path:
    from PIL import Image

    imgs = [Image.open(BUILD / f"icon_{s}.png") for s in [16, 32, 64, 128, 256]]
    ico = BUILD / "AppIcon.ico"
    imgs[0].save(ico, format="ICO", sizes=[(im.width, im.height) for im in imgs])
    return ico


def main() -> None:
    print(f"Source: {SVG}")
    for size in SIZES:
        out = BUILD / f"icon_{size}.png"
        rasterize(size, out)
        print(f"  → {out.name}")
    icns = build_icns()
    if icns:
        print(f"macOS icon: {icns}")
    ico = build_ico()
    print(f"Windows icon: {ico}")


if __name__ == "__main__":
    main()
