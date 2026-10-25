"""Renders index.html to mock.png at 1600px wide. Re-run: python render.py"""
from pathlib import Path
from playwright.sync_api import sync_playwright

here = Path(__file__).parent
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 900})
    pg.goto((here / "index.html").resolve().as_uri())
    pg.screenshot(path=str(here / "mock.png"), full_page=True)
    b.close()
