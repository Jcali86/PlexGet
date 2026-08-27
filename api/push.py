"""Web push notifications.

Notifications without an app store: since iOS 16.4 a page added to the home
screen can receive push, and the same code serves Android and desktop. The
identity keys (VAPID) are generated once and kept with the other secrets.

Subscriptions belong to a person, not a browser, so someone signed in on a
phone and a laptop is reached on both.
"""

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from py_vapid.utils import b64urlencode
from pywebpush import WebPushException, webpush

from config import config

KEY_PATH = Path(__file__).resolve().parent.parent / "data" / "vapid.json"
_keys = None
_signer = None


def keys():
    """VAPID identity for this server, generated on first use."""
    global _keys
    if _keys:
        return _keys
    if KEY_PATH.exists():
        _keys = json.loads(KEY_PATH.read_text())
        return _keys

    vapid = Vapid01()
    vapid.generate_keys()
    public = b64urlencode(
        vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )
    private = vapid.private_pem().decode()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_text(json.dumps({"public": public, "private": private}))
    KEY_PATH.chmod(0o600)
    _keys = {"public": public, "private": private}
    return _keys


def public_key():
    return keys()["public"]


def signer():
    """The key as a Vapid object, which is what pywebpush can actually use.

    Handed the PEM as a string it guesses the format, decides it is DER, and
    fails to parse it - so the parsing is done here instead, once, and the
    object is passed in.
    """
    global _signer
    if _signer is None:
        _signer = Vapid01.from_pem(keys()["private"].encode())
    return _signer


def _claims():
    contact = (config.get("app") or {}).get("contact", "mailto:admin@example.com")
    if not contact.startswith(("mailto:", "https:")):
        contact = f"mailto:{contact}"
    return {"sub": contact}


def ensure_table(db):
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plex_user_id TEXT NOT NULL,
            username TEXT,
            endpoint TEXT NOT NULL UNIQUE,
            keys_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_sent TIMESTAMP,
            failures INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions (plex_user_id);
    """)
    db.conn.commit()


def subscribe(db, user, subscription):
    """Remember where to reach this person. One row per device."""
    ensure_table(db)
    endpoint = subscription.get("endpoint")
    if not endpoint:
        raise ValueError("subscription has no endpoint")
    db.conn.execute(
        "INSERT INTO push_subscriptions (plex_user_id, username, endpoint, keys_json) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(endpoint) DO UPDATE SET plex_user_id = excluded.plex_user_id, "
        "username = excluded.username, keys_json = excluded.keys_json, failures = 0",
        (user["id"], user.get("username", ""), endpoint,
         json.dumps(subscription.get("keys", {}))),
    )
    db.conn.commit()


def unsubscribe(db, endpoint, plex_user_id=None):
    """Drop a subscription. When a user is given, only their own row is
    removed, so one household member cannot delete another's by endpoint."""
    ensure_table(db)
    if plex_user_id is None:
        db.conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    else:
        db.conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ? AND plex_user_id = ?",
            (endpoint, str(plex_user_id)),
        )
    db.conn.commit()


def _deliver(db, row, payload):
    try:
        webpush(
            subscription_info={"endpoint": row["endpoint"], "keys": json.loads(row["keys_json"])},
            data=json.dumps(payload),
            vapid_private_key=signer(),
            vapid_claims=dict(_claims()),
            timeout=10,
        )
        db.conn.execute(
            "UPDATE push_subscriptions SET last_sent = CURRENT_TIMESTAMP, failures = 0 WHERE id = ?",
            (row["id"],),
        )
        db.conn.commit()
        return True, ""
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        # 404/410 mean the browser threw the subscription away: stop trying.
        if status in (404, 410):
            db.conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (row["id"],))
        else:
            db.conn.execute(
                "UPDATE push_subscriptions SET failures = failures + 1 WHERE id = ?", (row["id"],)
            )
        db.conn.commit()
        return False, f"{status or 'error'}: {str(e)[:120]}"


def catalogue():
    """Every notification this app can send, as configured."""
    return (config.get("notifications") or {})


def describe():
    """The catalogue in a readable form, for showing someone what goes out."""
    meanings = {
        "arrived": "Whoever asked for a film, once it is actually playable in Plex",
        "requested": "You, when somebody asks for something the library does not have",
        "file_missing": "You, when a search turns up a film whose file has vanished",
        "test": "Only the person who presses the test button",
    }
    out = []
    for key, entry in catalogue().items():
        out.append({
            "key": key,
            "goes_to": meanings.get(key, ""),
            "enabled": bool(entry.get("enabled", True)),
            "title": entry.get("title", ""),
            "body": entry.get("body", ""),
        })
    return out


# Some notifications are the owner's business alone: who is asking for what,
# and which files have gone astray. Enforced here so a future call site cannot
# accidentally send them to the household.
OWNER_ONLY = {"requested", "file_missing"}


def owner_id():
    from api.plex_auth import allowed_users
    return next((uid for uid, info in allowed_users().items() if info["role"] == "owner"), None)


def send_event(db, key, plex_user_id, url="/", tag="", **fields):
    """Send one of the configured notifications, if it is switched on.

    Wording lives in config so it can be changed without touching code, and a
    missing placeholder must never take an import or a request down with it.
    """
    entry = catalogue().get(key)
    if not entry or not entry.get("enabled", True):
        return {"sent": 0, "failed": [], "skipped": key}
    if key in OWNER_ONLY:
        owner = owner_id()
        if not owner or str(plex_user_id) != str(owner):
            return {"sent": 0, "failed": [], "skipped": f"{key}: owner only"}
    try:
        title = entry.get("title", "PlexGet").format(**fields)
        body = entry.get("body", "").format(**fields)
    except (KeyError, IndexError, ValueError):
        title, body = entry.get("title", "PlexGet"), entry.get("body", "")
    return send_to_user(db, plex_user_id, title, body, url=url, tag=tag or key)


def send_to_user(db, plex_user_id, title, body, url="/", tag=""):
    """Notify one person on every device they have signed in on."""
    ensure_table(db)
    rows = db.conn.execute(
        "SELECT * FROM push_subscriptions WHERE plex_user_id = ?", (str(plex_user_id),)
    ).fetchall()
    payload = {"title": title, "body": body, "url": url, "tag": tag or title}
    sent, failed = 0, []
    for row in rows:
        ok, error = _deliver(db, row, payload)
        sent += 1 if ok else 0
        if not ok:
            failed.append(error)
    return {"sent": sent, "failed": failed}


def broadcast(db, title, body, url="/", tag="", exclude_user=None):
    """Notify everyone - a new film in the library, say."""
    ensure_table(db)
    rows = db.conn.execute("SELECT * FROM push_subscriptions").fetchall()
    payload = {"title": title, "body": body, "url": url, "tag": tag or title}
    sent, failed = 0, []
    for row in rows:
        if exclude_user and row["plex_user_id"] == str(exclude_user):
            continue
        ok, error = _deliver(db, row, payload)
        sent += 1 if ok else 0
        if not ok:
            failed.append(error)
    return {"sent": sent, "failed": failed}
