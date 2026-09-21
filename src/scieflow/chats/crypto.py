"""Bundle encryption through `age` or `gpg`.

No Python crypto dependency: both tools are discovered on PATH, the way the
news module finds agent CLIs. A bundle contains source code and tool output,
so encryption is the default and turning it off is an explicit, noisy choice.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

SUFFIXES = {"age": ".age", "gpg": ".gpg"}


class CryptoError(Exception):
    """Encryption or decryption failed, or no backend is installed."""


def available() -> list[str]:
    return [tool for tool in ("age", "gpg") if shutil.which(tool)]


def pick(method: str) -> tuple[str, str | None]:
    """Resolve the configured method against what is installed.

    Returns (method, warning). A configured backend that is missing falls back
    to the other one rather than failing the backup outright.
    """
    have = available()
    if not have:
        raise CryptoError(
            "no encryption backend found: install 'age' (recommended) or 'gpg', "
            "or pass --no-encrypt --yes to write an unencrypted bundle"
        )
    if method in have:
        return method, None
    return have[0], (
        f"encryption.method is {method!r} but {method} is not on PATH; using {have[0]}"
    )


def _run(cmd: list[str], interactive: bool = False) -> None:
    """Run a backend. Passphrase flows keep the terminal so the tool can prompt."""
    try:
        done = subprocess.run(cmd, capture_output=not interactive, text=True)
    except OSError as e:
        raise CryptoError(f"{cmd[0]} failed to start: {e}") from e
    if done.returncode != 0:
        tail = (done.stderr or "").strip().splitlines() if not interactive else []
        raise CryptoError(f"{cmd[0]} failed: {tail[-1] if tail else done.returncode}")


def encrypt(src: Path, method: str, recipient: str | None = None) -> Path:
    """Encrypt in place-ish: `bundle.zip` -> `bundle.zip.age`, source removed."""
    dest = src.with_name(src.name + SUFFIXES[method])
    passphrase = recipient is None
    if method == "age":
        cmd = ["age", "-o", str(dest)]
        cmd += ["-p"] if passphrase else ["-r", recipient]
        cmd += [str(src)]
    else:
        # No --batch: gpg must be free to prompt for the passphrase.
        cmd = ["gpg", "--yes", "--symmetric", "--cipher-algo", "AES256",
               "--output", str(dest), str(src)]
    _run(cmd, interactive=passphrase)
    if not dest.exists():
        raise CryptoError(f"{method} produced no output at {dest}")
    os.chmod(dest, 0o600)
    src.unlink(missing_ok=True)
    return dest


def decrypt(src: Path, dest: Path) -> Path:
    """Decrypt to `dest`, choosing the backend from the file suffix."""
    method = {".age": "age", ".gpg": "gpg"}.get(src.suffix)
    if method is None:
        raise CryptoError(f"{src.name}: not an encrypted bundle (.age or .gpg expected)")
    if method not in available():
        raise CryptoError(f"{src.name} needs {method}, which is not on PATH")
    if method == "age":
        cmd = ["age", "--decrypt", "-o", str(dest), str(src)]
    else:
        cmd = ["gpg", "--yes", "--output", str(dest), "--decrypt", str(src)]
    _run(cmd, interactive=True)
    if not dest.exists():
        raise CryptoError(f"{method} produced no output at {dest}")
    return dest


def is_encrypted(path: Path) -> bool:
    return path.suffix in SUFFIXES.values()
