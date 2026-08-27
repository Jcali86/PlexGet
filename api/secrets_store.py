"""Encryption for the Plex tokens we hold on people's behalf.

Acting on someone's Plex account - creating their playlists, adding to them -
needs their token, not a hash of it: a hash cannot sign a request. So the token
is kept, encrypted at rest, with the key in a file outside version control and
readable only by this user.
"""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


KEY_PATH = Path(__file__).resolve().parent.parent / "data" / "secret.key"


def _key():
    """The encryption key, created on first use and kept private to this user."""
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(KEY_PATH.parent, 0o700)
    key = Fernet.generate_key()
    # Open with 0600 from the start rather than writing world-readable and
    # tightening after - there is no window where another user can read it.
    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def encrypt(value):
    if not value:
        return ""
    return Fernet(_key()).encrypt(value.encode()).decode()


def decrypt(value):
    if not value:
        return ""
    try:
        return Fernet(_key()).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""

