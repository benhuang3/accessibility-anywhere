"""Run test modules with a hard time limit; on a hang, print where every thread is stuck and exit.

    cd tests
    python timed.py 60 test_pagekit browser.test_tools_e2e     (seconds per module, then module names)

These suites should take seconds, so a run past the limit is almost always a hang, not slowness.
"""

from __future__ import annotations

import faulthandler
import subprocess
import sys
import time


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    limit = float(argv[1])
    failed = 0
    for module in argv[2:]:
        t0 = time.time()
        # Each module in its own process: Playwright can't start twice in one process, and a hang in one
        # module mustn't hide the results of the others.
        code = ("import faulthandler, sys, unittest\n"
                f"faulthandler.dump_traceback_later({limit}, exit=True)\n"
                f"r = unittest.main(module={module!r}, argv=['x', '-v'], exit=False).result\n"
                "sys.exit(0 if r.wasSuccessful() else 1)\n")
        try:
            rc = subprocess.run([sys.executable, "-c", code], timeout=limit + 15).returncode
        except subprocess.TimeoutExpired:
            rc = "killed"
        took = time.time() - t0
        status = "OK" if rc == 0 else ("HUNG" if took >= limit or rc == "killed" else "FAILED")
        print(f"== {module}: {status} in {took:.1f} s", flush=True)
        failed += status != "OK"
    return 1 if failed else 0


if __name__ == "__main__":
    faulthandler.enable()
    sys.exit(main(sys.argv))
