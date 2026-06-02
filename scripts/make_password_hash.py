from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.security import hash_password  # noqa: E402


if __name__ == "__main__":
    password = getpass.getpass("Password to hash: ")
    confirm = getpass.getpass("Repeat password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")
    print(hash_password(password))
