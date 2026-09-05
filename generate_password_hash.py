"""Small utility to generate the SECRET_KEY and the administrator
password hash to paste into instance/config.py.

Usage:
    python generate_password_hash.py
"""

from __future__ import annotations

import getpass
import secrets

from werkzeug.security import generate_password_hash


def main() -> None:
    password = getpass.getpass("Desired administrator password: ")
    confirm = getpass.getpass("Confirm the password: ")
    if password != confirm:
        print("The two passwords do not match.")
        raise SystemExit(1)

    print("\nAdd these lines to instance/config.py:\n")
    print(f'ADMIN_PASSWORD_HASH = "{generate_password_hash(password)}"')
    print(f'SECRET_KEY = "{secrets.token_hex(32)}"')


if __name__ == "__main__":
    main()
