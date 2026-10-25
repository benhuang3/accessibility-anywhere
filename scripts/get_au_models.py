"""Download the LibreFace action-unit models into state/models.

    python scripts/get_au_models.py

The NuGet package is a zip of about 84 MB. This keeps only
LibreFace_AU_Encoder.onnx and LibreFace_AU_Presence.onnx.
A pair that is already there is left as it is.

USC research license: free for education, research and non-profit use; commercial use
needs a license from the USC Stevens Center. The files are not bundled in Ally.
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ally.senses.au import (ENCODER_FILE, LICENSE, PRESENCE_FILE, encoder_path,  # noqa: E402
                            model_dir, presence_path)

SOURCE = "https://www.nuget.org/api/v2/package/LibreFace"
# The package is about 84 MB. An error page is far smaller than this.
MIN_PACKAGE_BYTES = 1_000_000
MODEL_FILES = (ENCODER_FILE, PRESENCE_FILE)


def _urlopen(url: str, timeout: float = 120):
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "ally-get-au-models"})
    return urllib.request.urlopen(req, timeout=timeout)


def _present(state_dir: Path) -> bool:
    encoder = encoder_path(state_dir)
    presence = presence_path(state_dir)
    if not encoder.is_file() or not presence.is_file():
        return False
    if encoder.stat().st_size <= 0 or presence.stat().st_size <= 0:
        return False
    return True


def _save(dest: Path, payload: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".part")
    tmp.write_bytes(payload)
    tmp.replace(dest)


def _extract(data: bytes, state_dir: Path) -> None:
    """Write the two ONNX files only. Other package members stay in the zip."""
    found = {}
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        for info in package.infolist():
            name = Path(info.filename).name
            if name not in MODEL_FILES or name in found:
                continue
            # The file is written under state/models by its own name, never by the zip path.
            if ".." in Path(info.filename).parts:
                continue
            found[name] = package.read(info)
    missing = [name for name in MODEL_FILES if name not in found or not found[name]]
    if missing:
        listed = ", ".join(missing)
        raise OSError(f"LibreFace package has no {listed}")
    _save(encoder_path(state_dir), found[ENCODER_FILE])
    _save(presence_path(state_dir), found[PRESENCE_FILE])


def _download(state_dir: Path, opener) -> None:
    print(f"Downloading {SOURCE}")
    try:
        with opener(SOURCE, timeout=120) as resp:
            data = resp.read()
    except Exception as exc:
        raise OSError(exc) from exc
    if not isinstance(data, (bytes, bytearray)):
        raise OSError("LibreFace package looks wrong (no bytes)")
    data = bytes(data)
    if len(data) < MIN_PACKAGE_BYTES:
        raise OSError(
            f"LibreFace package looks wrong ({len(data)} bytes). It should be about 84 MB.")
    if not data.startswith(b"PK") or not zipfile.is_zipfile(io.BytesIO(data)):
        raise OSError("LibreFace package is not a zip.")
    _extract(data, state_dir)
    folder = model_dir(state_dir)
    print(f"Saved {ENCODER_FILE} and {PRESENCE_FILE} to {folder}")


def ensure_models(state_dir: Path, opener=None) -> int:
    """Save the two models under state_dir/models. Returns a process exit code."""
    print(LICENSE)
    print(f"Source: {SOURCE}")
    state_dir = Path(state_dir)
    if _present(state_dir):
        print(f"Already have {ENCODER_FILE} and {PRESENCE_FILE}")
        return 0
    opener = opener or _urlopen
    try:
        _download(state_dir, opener)
    except OSError as exc:
        print(f"Couldn't download the action-unit model ({exc})")
        return 1
    return 0


def _default_state() -> Path:
    from ally.paths import Paths

    return Paths().state_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/get_au_models.py")
    parser.add_argument("--state", type=Path, default=None, help="state folder (default: the app's state folder)")
    args = parser.parse_args(argv)
    state = args.state if args.state is not None else _default_state()
    return ensure_models(state)


if __name__ == "__main__":
    sys.exit(main())
