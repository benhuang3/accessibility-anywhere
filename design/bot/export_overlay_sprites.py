"""Renders the bear expressions for the Tk overlay into ally/overlay/bear/.
Needs: pip install playwright pillow (Chromium installed). Re-run: python export_overlay_sprites.py
The overlay window keys out one colour to look borderless, so Tk can't blend soft alpha. Each sprite
is rendered at the exact size it is shown at (no Tk scaling), the floor shadow is dropped, and
half-transparent edge pixels are flattened onto the bear's outline colour so the edge stays smooth."""
import re
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
OUT = HERE.parent.parent / "ally" / "overlay" / "bear"
EXPRESSIONS = ("idle", "happy", "concerned", "worried", "listening")
WIDTHS = (140, 175, 210, 280)  # 100%, 125%, 150%, 200% display scaling
OUTLINE = (0x9F, 0xBF, 0xCC)
KEEP = 90  # alpha at or above this becomes opaque; below it, transparent


def flatten(path: Path) -> None:
    im = Image.open(path).convert("RGBA")
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a == 255:
                continue
            if a < KEEP:
                px[x, y] = (0, 0, 0, 0)
                continue
            t = a / 255
            px[x, y] = (round(r * t + OUTLINE[0] * (1 - t)), round(g * t + OUTLINE[1] * (1 - t)),
                        round(b * t + OUTLINE[2] * (1 - t)), 255)
    im.crop(im.getbbox()).save(path, optimize=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name in EXPRESSIONS:
            svg = (HERE / "expr" / f"{name}.svg").read_text(encoding="utf-8")
            svg = re.sub(r'<g id="shadow">.*?</g>', "", svg, flags=re.S)
            for w in WIDTHS:
                h = round(w * 240 / 200)
                page = browser.new_page(viewport={"width": w, "height": h})
                sized = svg.replace('width="400" height="480"', f'width="{w}" height="{h}"')
                page.set_content(f"<html><body style='margin:0;background:transparent'>{sized}</body></html>")
                target = OUT / f"{name}-{w}.png"
                page.screenshot(path=str(target), omit_background=True)
                page.close()
                flatten(target)
        browser.close()
    print("wrote", len(EXPRESSIONS) * len(WIDTHS), "sprites to", OUT)


if __name__ == "__main__":
    main()
