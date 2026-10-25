#!/usr/bin/env python3
"""Ally: an always-on, OS-level accessibility agent.

    python run.py            start Ally (restarts itself if it crashes)
    python run.py doctor     check your setup
    python run.py profile show|delete   inspect or delete face setup data
    python run.py --help     all commands
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.version_info < (3, 11):
    sys.exit("Ally needs Python 3.11 or newer (you have %d.%d)." % sys.version_info[:2])

from ally.cli import main  # noqa: E402


def _profile_command(action: str) -> int:
    from ally.config import ConfigManager
    from ally.paths import Paths
    from ally.profile_store import ProfileStore

    paths = Paths()
    config = ConfigManager(paths.config)
    config.reload()
    store = ProfileStore(paths, config.data)
    if action == "delete":
        store.delete()
        result = store.status()
        print("Local face profile deleted.")
        if result["sync_enabled"]:
            print("Cloud profile deleted." if result["synced"] else "Cloud deletion could not be confirmed.")
            return 0 if result["synced"] else 1
        return 0
    profile = store.load()
    if profile is None:
        print("No face profile is saved.")
    else:
        updated = profile.get("updated")
        when = time.strftime("%Y-%m-%d", time.localtime(updated)) if isinstance(updated, (int, float)) else "date unknown"
        sections = [name for name, value in (("eye tracking", profile.get("gaze")),
                                              ("face sensing", (profile.get("senses") or {}).get("baseline")),
                                              ("action units", (profile.get("au") or {}).get("baseline"))) if value]
        print(f"Face profile saved ({when}): {', '.join(sections) or 'no calibration sections'}.")
    status = store.status()
    print("Sync: " + ("on, synced" if status["synced"] else "on, not synced")
          if status["sync_enabled"] else "Sync: off")
    return 0

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "profile" and sys.argv[2] in ("show", "delete"):
        sys.exit(_profile_command(sys.argv[2]))
    sys.exit(main())
