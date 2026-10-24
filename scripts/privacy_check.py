"""Prove the camera helper and the input helper do not use the network or write images.

    python scripts/privacy_check.py [--seconds 60]

Ally must be stopped (`python run.py stop`). The face model must already be on disk: its one-time
download is the only network use this feature should ever need.

Do these two checks by hand as well:
1. Turn Wi-Fi off and run this command again. It should still pass.
2. Open Resource Monitor (resmon), open the Network tab, and confirm the camera helper and the
   input helper show no network use. Save a screenshot with the report.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Running as a script: the repo root has to be importable.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ally.paths import CODE_ROOT, Paths
from ally.platforms.base import NO_WINDOW
from ally.senses.matrix import camera_command, input_command
from ally.supervisor import current_port, listening

MEDIA_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".mp4", ".avi", ".mov",
             ".mkv", ".webm", ".wmv"}
MANUAL = (
    "Do these two checks by hand as well:\n"
    "1. Turn Wi-Fi off and run this command again. It should still pass.\n"
    "2. Open Resource Monitor (resmon), open the Network tab, and confirm the camera helper and the\n"
    "   input helper show no network use. Save a screenshot with this report."
)


def ally_is_running(paths: Paths | None = None) -> bool:
    paths = paths or Paths()
    try:
        return bool(listening(current_port(paths)))
    except (OSError, KeyError, TypeError, ValueError):
        return False


def _psutil():
    try:
        import psutil
    except Exception:
        return None
    return psutil


def family(pid: int) -> set[int]:
    """This process and its children. A helper that spawned something still counts."""
    found = {int(pid)}
    ps = _psutil()
    if ps is not None:
        try:
            proc = ps.Process(int(pid))
            for child in proc.children(recursive=True):
                found.add(int(child.pid))
            return found
        except Exception:
            pass
    found |= _children_fallback(int(pid))
    return found


def _children_fallback(pid: int) -> set[int]:
    if sys.platform != "win32":
        return _children_ps(pid)
    try:
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Csv -NoTypeInformation"],
            capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if cp.returncode != 0:
        return set()
    parents: dict[int, int] = {}
    for line in cp.stdout.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            parents[int(parts[0])] = int(parts[1])
        except ValueError:
            continue
    found: set[int] = set()
    wave = {pid}
    while wave:
        nxt = {child for child, parent in parents.items() if parent in wave and child not in found}
        found |= nxt
        wave = nxt
    return found


def _children_ps(pid: int) -> set[int]:
    try:
        cp = subprocess.run(["ps", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    parents: dict[int, int] = {}
    for line in cp.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                parents[int(parts[0])] = int(parts[1])
            except ValueError:
                continue
    found: set[int] = set()
    wave = {pid}
    while wave:
        nxt = {child for child, parent in parents.items() if parent in wave and child not in found}
        found |= nxt
        wave = nxt
    return found


def endpoints_for(pids: set[int]) -> list[dict] | None:
    """TCP/UDP endpoints owned by these pids. None means the check itself failed."""
    if not pids:
        return []
    ps = _psutil()
    if ps is not None:
        try:
            return _psutil_endpoints(ps, pids)
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            rows = _powershell_endpoints(pids)
        except Exception:
            rows = None
        if rows is not None:
            return rows
    try:
        return _netstat_endpoints(pids)
    except Exception:
        return None


def network_hits(root_pids: list[int]) -> list[dict] | None:
    pids: set[int] = set()
    for pid in root_pids:
        pids |= family(pid)
    return endpoints_for(pids)


def _psutil_endpoints(ps, pids: set[int]) -> list[dict]:
    rows = []
    for pid in pids:
        try:
            proc = ps.Process(pid)
        except Exception:
            continue
        try:
            try:
                conns = proc.net_connections(kind="inet")
            except AttributeError:
                conns = proc.connections(kind="inet")
        except Exception:
            continue
        for conn in conns:
            local = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else ""
            remote = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else ""
            rows.append({"pid": pid, "proto": str(getattr(conn, "type", "")), "local": local,
                         "remote": remote, "state": str(conn.status or "")})
    return rows


def _powershell_endpoints(pids: set[int]) -> list[dict] | None:
    pid_list = ",".join(str(int(pid)) for pid in pids)
    script = (
        f"$want = @({pid_list})\n"
        "Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { $want -contains $_.OwningProcess } | "
        "ForEach-Object { \"TCP $($_.LocalAddress):$($_.LocalPort) $($_.RemoteAddress):$($_.RemotePort) $($_.State) $($_.OwningProcess)\" }\n"
        "Get-NetUDPEndpoint -ErrorAction SilentlyContinue | Where-Object { $want -contains $_.OwningProcess } | "
        "ForEach-Object { \"UDP $($_.LocalAddress):$($_.LocalPort) *:* $($_.OwningProcess)\" }\n"
    )
    try:
        cp = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                            timeout=40, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0 and not (cp.stdout or "").strip():
        return None
    return _parse_endpoint_lines(cp.stdout)


def _parse_endpoint_lines(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("TCP", "UDP"):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        rows.append({"proto": parts[0], "local": parts[1], "remote": parts[2],
                     "state": parts[3] if parts[0] == "TCP" and len(parts) >= 5 else "", "pid": pid})
    return rows


def _netstat_endpoints(pids: set[int]) -> list[dict] | None:
    try:
        cp = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:
        return None
    return parse_netstat(cp.stdout, pids)


def parse_netstat(text: str, pids: set[int]) -> list[dict]:
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("TCP", "UDP"):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid not in pids:
            continue
        if parts[0] == "TCP" and len(parts) >= 5:
            local, remote, state = parts[1], parts[2], parts[3]
        else:
            local, remote, state = parts[1], parts[2] if len(parts) > 2 else "", ""
        rows.append({"proto": parts[0], "local": local, "remote": remote, "state": state, "pid": pid})
    return rows


def snapshot_media(roots: list[Path]) -> set[Path]:
    found: set[Path] = set()
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if not root.exists():
            continue
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in (".git", "__pycache__")]
            for name in files:
                path = Path(dirpath) / name
                if path.suffix.lower() in MEDIA_EXT:
                    found.add(path.resolve())
    return found


def new_media(before: set[Path], after: set[Path]) -> list[Path]:
    return sorted(after - before)


def kill_tree(pid: int) -> None:
    ps = _psutil()
    if ps is not None:
        try:
            proc = ps.Process(int(pid))
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        except Exception:
            pass
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))], capture_output=True, creationflags=NO_WINDOW)


def format_report(passed: bool, seconds: float, samples: int, problems: list[str], pids: list[int]) -> str:
    lines = [
        "Senses privacy check",
        f"time: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"seconds: {seconds:g}",
        f"samples: {samples}",
        "pids: " + (", ".join(str(pid) for pid in pids) if pids else "none"),
        f"result: {'PASS' if passed else 'FAIL'}",
        "",
    ]
    if problems:
        lines.append("Problems:")
        lines.extend(f"- {item}" for item in problems)
    else:
        lines.append("No network endpoints on the helpers or their children.")
        lines.append("No new image or video files under the repo or the state folder.")
    lines.append("")
    lines.append(MANUAL)
    lines.append("")
    return "\n".join(lines)


def _stop(proc) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except Exception:
        kill_tree(proc.pid)


def run(seconds: float = 60, camera: int = 0, *, paths: Paths | None = None, sleep=None,
        state_dir: Path | None = None) -> int:
    paths = paths or Paths()
    sleep = sleep or time.sleep
    print(MANUAL)
    print()
    if ally_is_running(paths):
        print("Ally is using the camera. Run python run.py stop first, then try again.")
        return 2
    model = paths.state_dir / "models" / "face_landmarker.task"
    if not model.exists():
        print("The face model isn't downloaded yet (state/models/face_landmarker.task).")
        print("That one-time download is the only network use this feature should need.")
        print("Start Ally once while online, run python run.py stop, then run this check again.")
        return 2
    state = state_dir or paths.state_dir
    roots = [CODE_ROOT, paths.state_dir]
    before = snapshot_media(roots)
    calib = state / "privacy_checks" / "calibration.json"
    calib.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        camera_command(model, calib, int(camera), 20, 10),
        input_command(),
    ]
    procs = []
    try:
        for command in commands:
            procs.append(subprocess.Popen(command, cwd=str(CODE_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                          creationflags=NO_WINDOW))
    except OSError as e:
        print(f"Couldn't start the helpers: {e}")
        for proc in procs:
            _stop(proc)
        return 2
    problems: list[str] = []
    samples = 0
    end = time.monotonic() + max(0.0, seconds)
    try:
        while True:
            hits = network_hits([proc.pid for proc in procs if proc.pid])
            samples += 1
            if hits is None:
                problems.append("couldn't read network endpoints")
            else:
                for hit in hits:
                    text = f"network endpoint pid {hit.get('pid')} {hit.get('proto')} {hit.get('local')} {hit.get('remote')} {hit.get('state')}".strip()
                    if text not in problems:
                        problems.append(text)
            fresh = new_media(before, snapshot_media(roots))
            for path in fresh:
                text = f"new image or video file: {path}"
                if text not in problems:
                    problems.append(text)
            if time.monotonic() >= end:
                break
            sleep(min(1.0, max(0.0, end - time.monotonic())))
    finally:
        for proc in procs:
            _stop(proc)
    passed = not problems
    report = format_report(passed, seconds, samples, problems, [proc.pid for proc in procs])
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = state / "privacy_checks" / f"{stamp}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Report: {out}")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/privacy_check.py")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--camera", type=int, default=0)
    args = parser.parse_args(argv)
    if args.seconds < 0:
        print("--seconds cannot be negative.")
        return 2
    return run(args.seconds, args.camera)


if __name__ == "__main__":
    sys.exit(main())
