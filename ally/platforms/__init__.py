"""Pick the adapter for the current operating system."""

from __future__ import annotations

import platform

from .base import Platform


def get_platform() -> Platform:
    system = platform.system()
    if system == "Darwin":
        from .macos import MacPlatform

        return MacPlatform()
    if system == "Windows":
        from .windows import WindowsPlatform

        return WindowsPlatform()
    if system == "Linux":
        from .linux import LinuxPlatform

        return LinuxPlatform()
    return Platform()
