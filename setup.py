#!/usr/bin/env python3
"""
First-run setup: set a password for Forge.
Run: python setup.py
Then paste the output into your .env file.
"""
import hashlib
import secrets
import getpass


def main():
    print("⚡ Forge — Auth Setup\n")
    password = getpass.getpass("Password (leave empty to disable auth): ").strip()

    if not password:
        print("\nNo password set — Forge will be open to anyone who can reach the port.")
        print("Remove or leave PASSWORD_HASH= empty in .env.")
        return

    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=salt.encode(), n=2**14, r=8, p=1).hex()
    hash_str = f"scrypt${salt}${key}"

    print("\nAdd this line to your .env file:\n")
    print(f"PASSWORD_HASH={hash_str}\n")


if __name__ == "__main__":
    main()
