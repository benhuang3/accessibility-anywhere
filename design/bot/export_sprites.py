"""Renders expr/*.svg to PNG sprites for Tkinter.
Needs: pip install playwright (Chromium installed). Re-run: python export_sprites.py
Per expression and height (128, 256): a transparent PNG, plus '-bg' composited on the
bubble surface colour (tokens.json light 'surface'), because Tk can't blend soft alpha well."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

import build_expr

HERE = Path(__file__).parent
SURFACE = json.loads((HERE.parent / "tokens" / "tokens.json").read_text(encoding="utf-8"))["color"]["light"]["surface"]
HEIGHTS = (128, 256)


def main():
    build_expr.main()
    (HERE / "png").mkdir(exist_ok=True)
    count = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for svg in sorted((HERE / "expr").glob("*.svg")):
            text = svg.read_text(encoding="utf-8")
            for h in HEIGHTS:
                w = round(h * 200 / 240)
                page = browser.new_page(viewport={"width": w, "height": h})
                sized = text.replace('width="400" height="480"', f'width="{w}" height="{h}"')
                for bg, suffix in (("transparent", ""), (SURFACE, "-bg")):
                    page.set_content(f"<html><body style='margin:0;background:{bg}'>{sized}</body></html>")
                    page.screenshot(path=str(HERE / "png" / f"{svg.stem}-{h}{suffix}.png"),
                                    omit_background=(bg == "transparent"))
                    count += 1
                page.close()
        browser.close()
    print("wrote", count, "pngs; surface", SURFACE)


if __name__ == "__main__":
    main()
