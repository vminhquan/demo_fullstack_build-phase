"""Device token storage: OS keychain (Windows Credential Manager / Secret Service on Ubuntu) via `keyring`.

Headless Ubuntu servers often have no Secret Service; the token then goes to a file only the user can read.
"""
from __future__ import annotations

import os
import stat

import keyring
from keyring.errors import KeyringError, NoKeyringError

from scenario_forge_bridge.config import APP_NAME, config_dir

KEY = "device-token"


def _file():
    return config_dir() / "device-token"


def _keyring_usable() -> bool:
    if os.environ.get("SF_BRIDGE_TOKEN_STORE") == "file":
        return False
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001
        return False
    # The "fail" / "null" backends mean no real keychain is available.
    return backend.__class__.__module__.split(".")[-1] not in {"fail", "null"} and getattr(backend, "priority", 1) > 0


def save_token(token: str) -> str:
    """Stores the token and returns where it went ("keyring" or the file path)."""
    if _keyring_usable():
        try:
            keyring.set_password(APP_NAME, KEY, token)
            _file().unlink(missing_ok=True)
            return "keyring"
        except (KeyringError, NoKeyringError):
            pass
    target = _file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(token, encoding="utf-8")
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # Windows ACLs already limit the per-user AppData folder
        pass
    return str(target)


def load_token() -> str | None:
    if _keyring_usable():
        try:
            token = keyring.get_password(APP_NAME, KEY)
            if token:
                return token
        except (KeyringError, NoKeyringError):
            pass
    try:
        return _file().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def delete_token() -> None:
    if _keyring_usable():
        try:
            keyring.delete_password(APP_NAME, KEY)
        except (KeyringError, NoKeyringError, Exception):  # noqa: BLE001 - nothing stored
            pass
    _file().unlink(missing_ok=True)
