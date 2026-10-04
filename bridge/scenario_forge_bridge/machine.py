from __future__ import annotations

import platform
import socket


def hostname() -> str:
    return socket.gethostname()


def os_label() -> str:
    """e.g. "Ubuntu 22.04.4 LTS" / "Windows 11 (10.0.22631)"."""
    system = platform.system()
    if system == "Linux":
        try:
            info = platform.freedesktop_os_release()
            return info.get("PRETTY_NAME") or f"Linux {platform.release()}"
        except OSError:
            return f"Linux {platform.release()}"
    if system == "Windows":
        return f"Windows {platform.release()} ({platform.version()})"
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return f"{system} {platform.release()}"
