"""Собирает PNG-иконки разных размеров, .icns (macOS) и .ico (Windows).

Требует: Pillow, и один из (resvg-py | cairosvg | svglib+reportlab) для
честного рендера icon.svg — если ни один не доступен, используется грубый
fallback (перерисовка формы вручную через PIL, без учёта реального SVG).
resvg-py — предпочтительный вариант на Windows: чистый Rust-бинарь в wheel,
без системных зависимостей (в отличие от cairosvg/pycairo, которым нужна
нативная libcairo, и от svglib+reportlab, которые на этом icon.svg падают
на клип-пути скруглённого прямоугольника).
На macOS для .icns использует системный `iconutil`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Безопасная кодировка вывода — см. _venv.use_utf8_console.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _venv import use_utf8_console  # noqa: E402

use_utf8_console()

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "apps" / "desktop" / "frontend"
BUILD = ROOT / "build" / "icons"
BUILD.mkdir(parents=True, exist_ok=True)

SVG = FRONTEND / "icon.svg"
SIZES = [16, 32, 64, 128, 256, 512, 1024]


def rasterize_with_resvg(size: int, out: Path) -> None:
    import resvg_py

    png_bytes = resvg_py.svg_to_bytes(svg_path=str(SVG), width=size, height=size)
    out.write_bytes(bytes(png_bytes))


def rasterize_with_cairosvg(size: int, out: Path) -> None:
    import cairosvg

    cairosvg.svg2png(url=str(SVG), write_to=str(out), output_width=size, output_height=size)


def rasterize_with_svglib(size: int, out: Path) -> None:
    """Fallback без нативного cairo: svglib+reportlab, чистый Python."""
    from reportlab.graphics import renderPM
    from svglib.svglib import svg2rlg

    drawing = svg2rlg(str(SVG))
    scale = size / drawing.width
    drawing.width = drawing.height = size
    drawing.scale(scale, scale)
    renderPM.drawToFile(drawing, str(out), fmt="PNG", bg=0x000000, configPIL={"transparent": True})


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
    for renderer in (rasterize_with_resvg, rasterize_with_cairosvg, rasterize_with_svglib):
        try:
            renderer(size, out)
            return
        except Exception:
            pass
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

    # Pillow's ICO writer downsamples FROM the saved image — passing the
    # smallest (16x16) as base silently produces a single 16x16-only .ico
    # (blurry/blank-looking once Windows upscales it for the desktop icon).
    # Must save from the largest source so every requested size gets a
    # properly downsampled frame.
    sizes = [16, 32, 64, 128, 256]
    imgs = [Image.open(BUILD / f"icon_{s}.png") for s in sizes]
    ico = BUILD / "AppIcon.ico"
    imgs[-1].save(ico, format="ICO", sizes=[(im.width, im.height) for im in imgs])
    return ico


def main() -> None:
    print(f"Source: {SVG}")
    for size in SIZES:
        out = BUILD / f"icon_{size}.png"
        rasterize(size, out)
        print(f"  -> {out.name}")
    icns = build_icns()
    if icns:
        print(f"macOS icon: {icns}")
    ico = build_ico()
    print(f"Windows icon: {ico}")


if __name__ == "__main__":
    main()
