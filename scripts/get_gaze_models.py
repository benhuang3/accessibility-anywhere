"""Download MobileGaze ONNX files into state/models.

    python scripts/get_gaze_models.py
    python scripts/get_gaze_models.py mobileone_s0 resnet18

A file that is already there is left as it is. The default names are mobileone_s0
and resnet18 (mobileone_s0_gaze.onnx and resnet18_gaze.onnx).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ally.gaze_cnn import model_file, model_names  # noqa: E402

URL = "https://github.com/yakhyo/gaze-estimation/releases/download/weights/{name}_gaze.onnx"
DEFAULT_MODELS = ("mobileone_s0", "resnet18")
MIN_BYTES = 100_000


def _urlopen(url: str, timeout: float = 120):
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "ally-get-gaze-models"})
    return urllib.request.urlopen(req, timeout=timeout)


def _download(name: str, dest: Path, opener) -> None:
    url = URL.format(name=name)
    print(f"Downloading {dest.name}")
    try:
        with opener(url, timeout=120) as resp:
            data = resp.read()
    except Exception as e:
        raise OSError(e) from e
    if not isinstance(data, (bytes, bytearray)) or len(data) < MIN_BYTES:
        size = len(data) if isinstance(data, (bytes, bytearray)) else 0
        raise OSError(f"{dest.name} looks wrong ({size} bytes)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".part")
    tmp.write_bytes(bytes(data))
    tmp.replace(dest)


def ensure_models(state_dir: Path, names: list[str], opener=None) -> int:
    """Save any missing models under state_dir/models. Returns a process exit code."""
    try:
        wanted = model_names(",".join(names))
    except ValueError as e:
        print(e)
        return 2
    state_dir = Path(state_dir)
    opener = opener or _urlopen
    try:
        for name in wanted:
            dest = model_file(state_dir, name)
            if dest.is_file() and dest.stat().st_size > 0:
                print(f"Already have {dest.name}")
                continue
            _download(name, dest, opener)
            print(f"Saved {dest}")
    except OSError as e:
        print(f"Couldn't download the gaze model ({e})")
        return 1
    return 0


def _default_state() -> Path:
    from ally.paths import Paths

    return Paths().state_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/get_gaze_models.py")
    parser.add_argument("names", nargs="*", help="models to fetch (default: mobileone_s0 resnet18)")
    parser.add_argument("--state", type=Path, default=None, help="state folder (default: the app's state folder)")
    args = parser.parse_args(argv)
    names = list(args.names) or list(DEFAULT_MODELS)
    state = args.state if args.state is not None else _default_state()
    return ensure_models(state, names)


if __name__ == "__main__":
    sys.exit(main())
