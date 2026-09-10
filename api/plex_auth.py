"""Sign in with Plex.

The request page is open to anyone who can reach it, which was fine for one
household and is not fine once the link is shared. This authenticates people
against Plex itself: they log in with their own Plex account, and only the
server owner and the people the server is shared with are let through.

It is the standard PIN flow third-party Plex apps use, so the same endpoints
serve a browser page, a home-screen web app, or a native app later.
"""

import secrets
import threading
import time
import uuid
from urllib.parse import quote

import requests

from api.secrets_store import decrypt, encrypt
from config import config

PLEX_TV = "https://plex.tv/api/v2"
PRODUCT = "Plex-Ops"
SESSION_DAYS = 90

# One client identifier per install, kept for the life of the process.
CLIENT_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "plex-ops.local"))

# Seconds to wait on plex.tv, or on the local server, for the roster and for
# this server's identity. Both answer in well under a second when they answer
# at all, and the library's default of thirty turned a plex.tv wobble into a
# half-minute stall on every request in the house.
ROSTER_TIMEOUT = 10
# How long a refresh that failed stands before it is tried again. Every
# household request asks for the roster, and without this each of them paid
# the timeout in turn.
ROSTER_RETRY = 60

_allowed_cache = {"at": 0.0, "users": {}, "failed_at": 0.0}
_allowed_lock = threading.Lock()
_machine_id = None


def _headers(extra=None):
    headers = {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT,
        "X-Plex-Client-Identifier": CLIENT_ID,
    }
    headers.update(extra or {})
    return headers


def create_pin(origin=""):
    """Start a login, offering both ways in.

    Plex has two shapes of PIN and they are not interchangeable: the short
    four-character one is what plex.tv/link accepts typed in, and the long
    "strong" one is what the app.plex.tv/auth link carries. Asking for one
    breaks the other - a short code in the direct link is rejected outright -
    so both are issued and whichever the person completes is the one that
    counts.

    `origin` is where Plex sends the person after the tapped flow finishes.
    Without it the sign-in tab just stops at Plex's own success page, which
    reads as the app having lost them.
    """
    def _pin(strong):
        r = requests.post(
            f"{PLEX_TV}/pins", headers=_headers(),
            data={"strong": "true" if strong else "false"}, timeout=15,
        )
        r.raise_for_status()
        return r.json()

    typed = _pin(strong=False)
    tapped = _pin(strong=True)
    auth_url = (
        "https://app.plex.tv/auth#?clientID=" + CLIENT_ID +
        "&code=" + tapped["code"] +
        "&context%5Bdevice%5D%5Bproduct%5D=" + PRODUCT
    )
    if origin:
        auth_url += "&forwardUrl=" + quote(origin, safe="")
    return {
        "id": typed["id"],                 # the code you type
        "code": typed["code"],
        "link_id": tapped["id"],           # the link you tap
        "expires_in": typed.get("expiresIn", 900),
        "link_url": "https://plex.tv/link",
        "auth_url": auth_url,
    }


# -- issued pins ------------------------------------------------------------
# On disk rather than in memory: the poll endpoint only asks plex.tv about
# pins this server issued, so a restart that forgot them left every sign-in
# then in flight polling for ever - the person linked their account, Plex said
# so, and the page just sat there saying "waiting".

PIN_LIFE = 1800   # seconds a pin stays askable-about; Plex's own last 900


def ensure_pins(db):
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_pins (
            pin_id INTEGER PRIMARY KEY,
            issued_at REAL NOT NULL,
            polls INTEGER NOT NULL DEFAULT 0
        )""")
    db.conn.commit()


def remember_pins(db, *ids):
    ensure_pins(db)
    db.conn.execute("DELETE FROM auth_pins WHERE issued_at < ?",
                    (time.time() - PIN_LIFE,))
    for pin_id in ids:
        if pin_id:
            db.conn.execute(
                "INSERT OR REPLACE INTO auth_pins (pin_id, issued_at, polls) "
                "VALUES (?, ?, 0)", (int(pin_id), time.time()))
    db.conn.commit()


def pin_known(db, pin_id):
    """Did this server issue this pin, recently enough to still care?"""
    if not pin_id:
        return False
    ensure_pins(db)
    row = db.conn.execute(
        "SELECT 1 FROM auth_pins WHERE pin_id = ? AND issued_at >= ?",
        (int(pin_id), time.time() - PIN_LIFE)).fetchone()
    return row is not None


def count_poll(db, pin_id, ceiling):
    """One more look at this pin; False once it has been looked at too often."""
    ensure_pins(db)
    db.conn.execute("UPDATE auth_pins SET polls = polls + 1 WHERE pin_id = ?",
                    (int(pin_id),))
    db.conn.commit()
    row = db.conn.execute("SELECT polls FROM auth_pins WHERE pin_id = ?",
                          (int(pin_id),)).fetchone()
    if row and row[0] > ceiling:
        db.conn.execute("DELETE FROM auth_pins WHERE pin_id = ?", (int(pin_id),))
        db.conn.commit()
        return False
    return True


def check_pin(pin_id):
    """Has the person finished logging in? Returns their Plex token or None."""
    r = requests.get(f"{PLEX_TV}/pins/{pin_id}", headers=_headers(), timeout=15)
    if not r.ok:
        return None
    return r.json().get("authToken")


def plex_identity(token):
    """Who does this Plex token belong to?"""
    r = requests.get(f"{PLEX_TV}/user", headers=_headers({"X-Plex-Token": token}), timeout=15)
    if not r.ok:
        return None
    data = r.json()
    return {
        "id": str(data.get("id")),
        "username": data.get("username") or data.get("title") or "",
        "email": data.get("email") or "",
        "thumb": data.get("thumb") or "",
    }


def machine_id():
    """This server's identifier, asked for once and kept - it never changes.

    None when the server could not be asked, which callers treat as not
    knowing rather than as an answer.
    """
    global _machine_id
    if _machine_id is None:
        from plexapi.server import PlexServer
        try:
            _machine_id = PlexServer(config["plex"]["url"], config["plex"]["token"],
                                     timeout=ROSTER_TIMEOUT).machineIdentifier
        except Exception:
            return None
    return _machine_id


def _refresh_allowed():
    """Ask plex.tv who is shared here. Called under the lock, and only then."""
    from plexapi.myplex import MyPlexAccount

    people = {}
    try:
        account = MyPlexAccount(token=config["plex"]["token"], timeout=ROSTER_TIMEOUT)
        people[str(account.id)] = {"username": account.username, "role": "owner"}

        # Only friends shared on THIS server, not everyone on the owner's
        # account. account.users() spans every server the owner runs and their
        # Plex Home members; admitting all of them would let someone shared on
        # a different box sign in here and spend the shared AI budget.
        this_id = machine_id()

        for user in account.users():
            if this_id is not None:
                shared_here = any(
                    getattr(srv, "machineIdentifier", None) == this_id
                    for srv in (getattr(user, "servers", None) or [])
                )
                if not shared_here:
                    continue
            people[str(user.id)] = {"username": user.title, "role": "shared"}
    except Exception:
        # Never fail open: with no list, only the owner's own token works. The
        # failure is remembered so the next request takes what is in hand
        # rather than paying the same wait again.
        _allowed_cache["failed_at"] = time.time()
        return _allowed_cache["users"]
    _allowed_cache.update({"at": time.time(), "users": people, "failed_at": 0.0})
    return people


def allowed_users(max_age=600, insist=False):
    """Everyone entitled to use this: the server owner, plus its shared users.

    Refreshed from plex.tv every few minutes, and by one request at a time.
    The page fires half a dozen requests at once on load, and each of them
    used to set off a trip of its own to plex.tv; now the first goes and the
    rest take the roster already in hand, however old. A refresh that fails
    leaves the last known roster standing and is not tried again for a while,
    so an outage at plex.tv costs one bounded wait rather than one per request.

    `insist` is for the sign-in itself, which is rare and worth waiting for:
    it takes no rest after a failure and queues behind a refresh under way,
    because refusing somebody at the door on a stale roster is worse than
    keeping them there a few seconds.
    """
    now = time.time()
    if _allowed_cache["users"] and now - _allowed_cache["at"] < max_age:
        return _allowed_cache["users"]
    if not insist and now - _allowed_cache["failed_at"] < ROSTER_RETRY:
        return _allowed_cache["users"]
    if not _allowed_lock.acquire(blocking=insist):
        return _allowed_cache["users"]
    try:
        # Whoever held the lock may have just finished the same job.
        if _allowed_cache["users"] and time.time() - _allowed_cache["at"] < max_age:
            return _allowed_cache["users"]
        return _refresh_allowed()
    finally:
        _allowed_lock.release()


def server_access_token(account_token):
    """The token that actually opens THIS server for this person.

    The pin flow hands back a plex.tv ACCOUNT token, and the server refuses
    that outright for anyone but the owner - Plex's own clients exchange it
    for a per-server access token, and so must we. Skipping the exchange is
    why every shared user's sign-in "succeeded" and then died at the first
    check: their session held a token the server would never accept.

    Returns the per-server token, or None when their resources genuinely do
    not include this server - which means the share invite is still sitting
    unaccepted, and nothing they do here will work until it is. A lookup that
    merely errors falls back to the account token, so a plex.tv wobble does
    not lock the owner out.
    """
    this_id = machine_id()
    if this_id is None:
        return account_token
    try:
        r = requests.get(
            f"{PLEX_TV}/resources",
            headers=_headers({"X-Plex-Token": account_token}),
            params={"includeHttps": 1}, timeout=15,
        )
        if not r.ok:
            return account_token
        for res in r.json():
            if res.get("clientIdentifier") == this_id:
                return res.get("accessToken") or account_token
        return None      # share not accepted: this server is not among theirs
    except Exception:
        return account_token


def authorise(identity):
    """Is this Plex user allowed in?"""
    if not identity:
        return None
    people = allowed_users(insist=True)
    match = people.get(identity["id"])
    if not match:
        return None
    return {**identity, "role": match["role"]}


# -- home-screen handoff ----------------------------------------------------
# An iOS home-screen app has its own cookie jar, so installing the app used to
# mean signing in twice - once in Safari, once in the app, which reads as the
# sign-in not having taken. The manifest is generated per request, so when a
# SIGNED-IN browser fetches it during add-to-home-screen, a one-time code goes
# into start_url; the app's first launch trades it for a session of its own.

HANDOFF_LIFE = 172800   # seconds; two days between adding and first opening


def ensure_handoff(db):
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_handoff (
            code TEXT PRIMARY KEY,
            plex_user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            plex_token_enc TEXT,
            created_at REAL NOT NULL
        )""")
    db.conn.commit()


def mint_handoff(db, user):
    ensure_handoff(db)
    db.conn.execute("DELETE FROM auth_handoff WHERE created_at < ?",
                    (time.time() - HANDOFF_LIFE,))
    code = secrets.token_urlsafe(24)
    db.conn.execute(
        "INSERT INTO auth_handoff (code, plex_user_id, username, role, "
        "plex_token_enc, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (code, user["id"], user["username"], user["role"],
         encrypt(user.get("plex_token") or ""), time.time()))
    db.conn.commit()
    return code


def redeem_handoff(db, code):
    """One code, one session, once. Returns a fresh session token or None."""
    ensure_handoff(db)
    row = db.conn.execute(
        "SELECT * FROM auth_handoff WHERE code = ? AND created_at >= ?",
        (code, time.time() - HANDOFF_LIFE)).fetchone()
    if row is None:
        return None
    db.conn.execute("DELETE FROM auth_handoff WHERE code = ?", (code,))
    db.conn.commit()
    person = {"id": row["plex_user_id"], "username": row["username"],
              "role": row["role"]}
    return start_session(db, person, decrypt(row["plex_token_enc"]))


# -- sessions ---------------------------------------------------------------

def ensure_table(db):
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            plex_user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            plex_token_enc TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (plex_user_id);
    """)
    # The table predates the stored token, and CREATE TABLE IF NOT EXISTS will
    # not add a column to a table that already exists.
    columns = {row[1] for row in db.conn.execute("PRAGMA table_info(sessions)")}
    if "plex_token_enc" not in columns:
        db.conn.execute("ALTER TABLE sessions ADD COLUMN plex_token_enc TEXT")
    db.conn.commit()


def start_session(db, person, plex_token=""):
    """Keep the person's Plex token, encrypted, so their playlists can be theirs."""
    ensure_table(db)
    # A new sign-in is a natural, infrequent moment to clear out sessions past
    # their life - and the encrypted tokens they hold - rather than letting
    # dead rows accumulate forever.
    db.conn.execute(
        "DELETE FROM sessions WHERE created_at <= datetime('now', ?)",
        (f"-{SESSION_DAYS} days",),
    )
    token = secrets.token_urlsafe(32)
    db.conn.execute(
        "INSERT INTO sessions (token, plex_user_id, username, role, plex_token_enc) "
        "VALUES (?, ?, ?, ?, ?)",
        (token, person["id"], person["username"], person["role"], encrypt(plex_token)),
    )
    db.conn.commit()
    return token


def session_user(db, token):
    """Who is this session, if it is still valid?"""
    if not token:
        return None
    ensure_table(db)
    row = db.conn.execute(
        "SELECT * FROM sessions WHERE token = ? "
        "AND created_at > datetime('now', ?)",
        (token, f"-{SESSION_DAYS} days"),
    ).fetchone()
    if row is None:
        return None
    # Re-check against the live (10-min cached) roster rather than trusting the
    # role frozen into the row at sign-in. If the owner has since withdrawn
    # someone's Plex share, their session stops working within that window
    # instead of lasting the full session lifetime; and the role used is the
    # current one, not whatever it was months ago. Fail closed only when the
    # roster is actually known - an outage that empties it must not sign
    # everyone out, so an empty roster leaves the stored role in place.
    current = allowed_users()
    if current:
        match = current.get(str(row["plex_user_id"]))
        if match is None:
            return None
        role = match["role"]
    else:
        role = row["role"]
    db.conn.execute("UPDATE sessions SET last_seen = CURRENT_TIMESTAMP WHERE token = ?", (token,))
    db.conn.commit()
    return {
        "id": row["plex_user_id"],
        "username": row["username"],
        "role": role,
        "plex_token": decrypt(row["plex_token_enc"] if "plex_token_enc" in row.keys() else ""),
    }


# How long a verdict on a token stands. A token that opened the server five
# minutes ago has not died since, near enough, and asking on every page load
# meant a wait on Plex in front of a page that had nothing else to wait for.
# Only good news is kept, so a token that has actually died is caught on the
# next ask; a server that could not be asked counts as good news for a
# shorter while, since unreachable is not the same as refused.
TOKEN_ALIVE_FOR = 300
TOKEN_UNKNOWN_FOR = 60
TOKEN_CHECK_TIMEOUT = 4
_alive = {}


def token_alive(plex_token):
    """Is this person's stored Plex token still good?

    Asked of the local server, so it costs a few milliseconds. A password
    change or a sign-out-everywhere kills every token at once, and without
    this the app looks signed in and only admits otherwise when a search
    fails - which reads as the app having frozen.
    """
    if not plex_token:
        return False
    now = time.time()
    held = _alive.get(plex_token)
    if held and now - held[0] < held[1]:
        return True
    if len(_alive) > 500:
        for stale in [t for t, (at, life) in _alive.items() if now - at >= life]:
            _alive.pop(stale, None)
    try:
        r = requests.get(
            config["plex"]["url"].rstrip("/") + "/library/sections",
            headers={"X-Plex-Token": plex_token, "Accept": "application/json"},
            timeout=TOKEN_CHECK_TIMEOUT,
        )
    except Exception:
        _alive[plex_token] = (now, TOKEN_UNKNOWN_FOR)
        return True   # Plex unreachable is not the same as a bad token
    if r.status_code == 401:
        _alive.pop(plex_token, None)
        return False
    _alive[plex_token] = (now, TOKEN_ALIVE_FOR)
    return True


def token_for(db, username):
    """The freshest stored Plex token for a household member, or None.

    Every sign-in leaves the per-server token behind, which is what lets the
    owner act for somebody who is not here - making a playlist that belongs to
    them rather than one of the owner's shared out. Newest first, because a
    token dies when they change their password or sign out everywhere, and the
    most recent sign-in is the likeliest to still be alive.
    """
    ensure_table(db)
    rows = db.conn.execute(
        "SELECT plex_token_enc FROM sessions WHERE username = ? "
        "AND plex_token_enc IS NOT NULL AND plex_token_enc != '' "
        "ORDER BY last_seen DESC", (username,)).fetchall()
    for row in rows:
        token = decrypt(row["plex_token_enc"])
        if token:
            return token
    return None


def member_session(db, username):
    """A member's Plex id and freshest token, or None.

    The id is what their watch history is filed under and the token is what
    reads it, so building a list out of somebody's own taste needs both.
    """
    ensure_table(db)
    rows = db.conn.execute(
        "SELECT plex_user_id, plex_token_enc FROM sessions WHERE username = ? "
        "AND plex_token_enc IS NOT NULL AND plex_token_enc != '' "
        "ORDER BY last_seen DESC", (username,)).fetchall()
    for row in rows:
        token = decrypt(row["plex_token_enc"])
        if token:
            return {"id": row["plex_user_id"], "token": token}
    return None


def members_with_tokens(db):
    """Everyone the owner could make a playlist for, newest sign-in first.

    Somebody who has never opened the app has left no token, so they are not
    offered - there would be no account to put the playlist in.
    """
    ensure_table(db)
    rows = db.conn.execute(
        "SELECT username, MAX(last_seen) AS seen FROM sessions "
        "WHERE plex_token_enc IS NOT NULL AND plex_token_enc != '' "
        "GROUP BY username ORDER BY seen DESC").fetchall()
    return [{"username": r["username"], "last_seen": r["seen"]} for r in rows]


def end_session(db, token):
    ensure_table(db)
    db.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    db.conn.commit()
