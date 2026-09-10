"""
Generates the Android launcher icon for the Intrinsic app.

The mark matches the one drawn in-app (_BrandMark): a rising four-point line
with a marked endpoint over a grounded baseline, on the brand indigo.

Produces:
  * legacy square icons for every mipmap density
  * a round variant, used by launchers that request one
  * a foreground layer for the adaptive icon, drawn inside the safe zone so
    the launcher can mask it to any shape without clipping the mark

Run:  python tools/generate_app_icon.py
"""

import pathlib

from PIL import Image, ImageDraw

RES = pathlib.Path(__file__).resolve().parents[1] / "app" / "android" / "app" / "src" / "main" / "res"

BRAND = (58, 91, 217)        # #3A5BD9 - the app's accent
BRAND_DEEP = (28, 45, 122)   # deeper tone for the gradient foot
INK = (255, 255, 255)

# Legacy icons are the full canvas; adaptive foregrounds must keep the mark
# within the middle ~66% so masking cannot crop it.
DENSITIES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
ADAPTIVE = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}

SUPERSAMPLE = 8  # draw large, then downsample for clean antialiasing


def draw_mark(size: int, scale: float, colour) -> Image.Image:
    """The line-chart mark, centred, on a transparent canvas."""
    s = size * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Map the in-app geometry into the requested fraction of the canvas.
    pad = (1 - scale) / 2

    def px(fx: float, fy: float) -> tuple[float, float]:
        return (s * (pad + fx * scale), s * (pad + fy * scale))

    stroke = max(2, int(s * scale * 0.105))

    baseline = [px(0.14, 0.86), px(0.86, 0.86)]
    d.line(baseline, fill=colour + (110,), width=int(stroke * 0.78), joint="curve")
    for point in baseline:
        d.ellipse(
            [point[0] - stroke * 0.39, point[1] - stroke * 0.39,
             point[0] + stroke * 0.39, point[1] + stroke * 0.39],
            fill=colour + (110,),
        )

    path = [px(0.14, 0.70), px(0.38, 0.48), px(0.56, 0.62), px(0.86, 0.24)]
    d.line(path, fill=colour + (255,), width=stroke, joint="curve")
    # Round the joins and the two ends, which PIL's `joint` does not cover.
    for point in path:
        d.ellipse(
            [point[0] - stroke / 2, point[1] - stroke / 2,
             point[0] + stroke / 2, point[1] + stroke / 2],
            fill=colour + (255,),
        )

    end = px(0.86, 0.24)
    dot = stroke * 1.05
    d.ellipse([end[0] - dot, end[1] - dot, end[0] + dot, end[1] + dot],
              fill=colour + (255,))

    return img.resize((size, size), Image.LANCZOS)


def brand_background(size: int, radius_fraction: float | None) -> Image.Image:
    """Brand-coloured plate, with a soft vertical gradient for depth."""
    s = size * SUPERSAMPLE
    plate = Image.new("RGBA", (s, s), BRAND + (255,))

    gradient = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)
    for y in range(s):
        t = y / max(1, s - 1)
        gd.line([(0, y), (s, y)], fill=BRAND_DEEP + (int(90 * t * t),))
    plate = Image.alpha_composite(plate, gradient)

    if radius_fraction is not None:
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, s - 1, s - 1], radius=int(s * radius_fraction), fill=255)
        plate.putalpha(mask)

    return plate.resize((size, size), Image.LANCZOS)


def circular_background(size: int) -> Image.Image:
    s = size * SUPERSAMPLE
    plate = brand_background(size, None).resize((s, s), Image.LANCZOS)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
    plate.putalpha(mask)
    return plate.resize((size, size), Image.LANCZOS)


def compose(background: Image.Image, mark: Image.Image) -> Image.Image:
    return Image.alpha_composite(background, mark)


def main() -> None:
    written = []

    for density, size in DENSITIES.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)

        square = compose(brand_background(size, 0.22), draw_mark(size, 0.62, INK))
        square.save(folder / "ic_launcher.png")
        written.append(folder / "ic_launcher.png")

        round_icon = compose(circular_background(size), draw_mark(size, 0.58, INK))
        round_icon.save(folder / "ic_launcher_round.png")
        written.append(folder / "ic_launcher_round.png")

    for density, size in ADAPTIVE.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        # Foreground only; the background is a flat colour resource, and the
        # mark is drawn small enough to survive any mask shape.
        fg = draw_mark(size, 0.40, INK)
        fg.save(folder / "ic_launcher_foreground.png")
        written.append(folder / "ic_launcher_foreground.png")

    for path in written:
        print(f"  {path.relative_to(RES.parents[4])}  ({path.stat().st_size:,} bytes)")
    print(f"\n{len(written)} files written")


if __name__ == "__main__":
    main()
