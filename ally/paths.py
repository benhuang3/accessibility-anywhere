"""Where Ally keeps its files. Set ALLY_HOME to run from another folder (tests do)."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CODE_ROOT = PACKAGE_DIR.parent  # folder holding run.py, extension/, dashboard/


class Paths:
    def __init__(self, home: Path | str | None = None):
        self.home = Path(home or os.environ.get("ALLY_HOME") or CODE_ROOT).resolve()

    @property
    def config(self) -> Path:
        return self.home / "config.toml"

    @property
    def state_dir(self) -> Path:
        return self.home / "state"

    @property
    def log_dir(self) -> Path:
        return self.home / "logs"

    @property
    def env_file(self) -> Path:
        return self.home / ".env"

    @property
    def token_file(self) -> Path:
        return self.state_dir / "token"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    def plugins_dir(self, folder: str = "plugins") -> Path:
        p = Path(folder)
        return p if p.is_absolute() else self.home / p

    # Static assets always come from the code folder.
    @property
    def dashboard_dir(self) -> Path:
        return CODE_ROOT / "dashboard"

    @property
    def extension_dir(self) -> Path:
        return CODE_ROOT / "extension"

    @property
    def run_script(self) -> Path:
        return CODE_ROOT / "run.py"

    def ensure(self) -> None:
        for d in (self.state_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ (existing variables win)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
