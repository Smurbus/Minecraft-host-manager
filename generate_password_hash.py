"""Petit utilitaire pour générer le SECRET_KEY et le hash du mot de passe
administrateur à coller dans instance/config.py.

Usage :
    python generate_password_hash.py
"""

from __future__ import annotations

import getpass
import secrets

from werkzeug.security import generate_password_hash


def main() -> None:
    password = getpass.getpass("Mot de passe administrateur souhaité : ")
    confirm = getpass.getpass("Confirmez le mot de passe : ")
    if password != confirm:
        print("Les deux mots de passe ne correspondent pas.")
        raise SystemExit(1)

    print("\nAjoutez ces lignes dans instance/config.py :\n")
    print(f'ADMIN_PASSWORD_HASH = "{generate_password_hash(password)}"')
    print(f'SECRET_KEY = "{secrets.token_hex(32)}"')


if __name__ == "__main__":
    main()
