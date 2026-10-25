"""Time each page-step action on one real website (no agent needed). Finds which step is slow.

    C:\\ally\\.venv\\Scripts\\python.exe scripts\\time_steps.py https://en.wikipedia.org/wiki/Accessibility
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from browser.harness import AllyBrowser  # noqa: E402

CALLS = [
    ("pageInfo", {}),
    ("pageBaseline", {}),
    ("stepOn", {"step": "clutter", "opts": {}}),
    ("stepOn", {"step": "buttons", "opts": {}}),
    ("stepOn", {"step": "menus", "opts": {"mode": "panel"}}),
    ("stepOff", {"step": "menus", "opts": {}}),
    ("pageVerify", {}),
    ("auditAtZooms", {"levels": [1.0, 1.25, 1.5, 1.75, 2.0]}),
    ("pageTeardown", {}),
]


def main(url: str) -> int:
    with AllyBrowser(headed=True) as ally:
        page = ally.page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.bring_to_front()
        page.wait_for_timeout(3000)
        print("elements on page:", page.evaluate("() => document.getElementsByTagName('*').length"), flush=True)
        for name, args in CALLS:
            t0 = time.time()
            try:
                out = ally.action(name, args)
                note = str(out)[:160]
            except Exception as e:
                note = f"ERROR {e}"[:200]
            print(f"{name:14} {args.get('step', ''):8} {time.time() - t0:6.2f} s  {note}", flush=True)
        errs = ally.worker_errors()
        if errs:
            print("worker errors:", errs[:5])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "https://en.wikipedia.org/wiki/Accessibility"))
