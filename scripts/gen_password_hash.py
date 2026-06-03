"""
Generate bcrypt password hashes for the Streamlit login (streamlit-authenticator).
Paste the printed hashes into .streamlit/secrets.toml (local) or the Streamlit
Community Cloud "Secrets" box — never commit plaintext passwords.

Usage:
  python scripts/gen_password_hash.py <password1> [<password2> ...]
"""
from __future__ import annotations

import sys

import streamlit_authenticator as stauth


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for pw in sys.argv[1:]:
        print(f"{pw!r:20s} -> {stauth.Hasher.hash(pw)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
