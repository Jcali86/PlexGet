#!/usr/bin/env python3
"""Check the setup: config, Plex, the libraries, and the optional extras.

Run this first, and again after any change to config.yaml. Every line is a
pass, a fail, or a skip, and a skip is fine - the optional pieces are optional.
Nothing here writes anything or sends anything anywhere.

    venv/bin/python test_connections.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "skip"

_failures = 0

# Values the example file ships. Left in place they are not configuration,
# they are a note to self, so they count as nothing being set at all.
PLACEHOLDERS = ("REPLACE_ME", "YOUR_", "CHANGE_ME")


def unset(value):
    text = (value or "").strip()
    return not text or any(text.startswith(p) for p in PLACEHOLDERS)


def say(state, text, detail=None):
    global _failures
    if state == FAIL:
        _failures += 1
    print(f"  [{state:4s}] {text}")
    if detail:
        print(f"          {detail}")


def plural(count, thing):
    return f"{count} {thing}" + ("" if count == 1 else "s")


def heading(text):
    print(f"\n--- {text} ---")


def check_config():
    """Is there a config at all, and does it parse?"""
    heading("Config")
    if not (ROOT / "config.yaml").exists():
        say(FAIL, "config.yaml", "not found - copy config.example.yaml to config.yaml")
        return None
    try:
        from config import config
    except SystemExit:
        # config.py has already said what is wrong with it, in more detail than
        # anything here could, so this only records that it went badly.
        say(FAIL, "config.yaml", "cannot be used as it stands - see above")
        return None
    except Exception as e:
        say(FAIL, "config.yaml", f"could not be read: {e}")
        return None
    if not isinstance(config, dict):
        say(FAIL, "config.yaml", "is not a set of settings - check the indentation")
        return None
    say(PASS, "config.yaml", f"{len(config)} sections")
    return config


def check_plex(config):
    """Is Plex there, and does the token still work?"""
    heading("Plex")
    settings = config.get("plex") or {}
    url, token = settings.get("url"), settings.get("token")
    if not url:
        say(FAIL, "plex.url", "not set")
        return None
    if unset(token):
        say(FAIL, "plex.token", "not set - see the README for getting one")
        return None

    try:
        from plexapi.server import PlexServer

        server = PlexServer(url, token, timeout=20)
    except Exception as e:
        message = str(e)
        if "401" in message or "unauthorized" in message.lower():
            say(FAIL, f"connect to {url}", "the token was refused - it has expired or belongs elsewhere")
        else:
            say(FAIL, f"connect to {url}", message[:200])
        return None

    say(PASS, f"connect to {url}", f"{server.friendlyName}, Plex {server.version}")
    try:
        sections = list(server.library.sections())
    except Exception as e:
        say(FAIL, "read the libraries", str(e)[:200])
        return server
    if not sections:
        say(FAIL, "read the libraries", "the server has none - add one in Plex first")
    else:
        listed = ", ".join(f"{s.title} ({s.type})" for s in sections)
        say(PASS, f"{len(sections)} libraries on the server", listed)
    return server


def check_libraries(plex_up):
    """Does each library named in config line up with one Plex knows?"""
    heading("Libraries in config")
    from scanner import libraries

    keys = libraries.keys()
    if not keys:
        say(FAIL, "the nas block", "no libraries listed - this app has nothing to look at")
        return

    for key in keys:
        folders = libraries.paths_for(key)
        if not folders:
            say(FAIL, key, "no folders listed")
            continue
        if not plex_up:
            say(SKIP, key, f"{plural(len(folders), 'folder')}; nothing from Plex to match it against")
            continue
        section = libraries.section_name(key)
        kind = libraries.kind(key) or "unknown"
        if section:
            manual = ", looked after by hand" if libraries.is_manual(key) else ""
            say(PASS, key, f"{plural(len(folders), 'folder')} -> Plex section \"{section}\" ({kind}){manual}")
        else:
            say(FAIL, key, "no Plex section shares these folders - "
                           "set libraries.sections in config to say which one owns it")


def check_storage(config):
    """Can the files actually be read from here?"""
    heading("Storage")
    from scanner import paths

    if (config.get("paths") or {}).get("roots"):
        say(PASS, "paths.roots", "set, so Plex's paths are translated before being read")
    else:
        say(SKIP, "paths.roots", "not set - the library folders are taken as they are, "
                                 "which is right when Plex reads them the same way")

    for store in paths.status():
        names = " or ".join(store["names"])
        if store["reachable"]:
            say(PASS, store["reachable"], "readable, and holds something")
        else:
            say(FAIL, names, "cannot be read, or is empty - an off drive looks like this too, "
                             "and while it does, nothing on it is reported missing")

    missing = [p for p in paths.library_paths() if not Path(p).is_dir()]
    if missing:
        say(FAIL, f"{len(missing)} library folders are not there",
            "; ".join(missing[:4]) + (" ..." if len(missing) > 4 else ""))
    else:
        say(PASS, "every library folder in config exists")


def check_tmdb(config):
    """TMDb fills in what Plex does not know. Optional."""
    heading("TMDb (optional - gap analysis and posters)")
    settings = config.get("tmdb") or {}
    key = settings.get("api_key")
    if unset(key):
        say(SKIP, "tmdb.api_key", "not set - gap analysis and poster fetching stay off")
        return
    base = settings.get("base_url") or "https://api.themoviedb.org/3"
    try:
        import requests

        resp = requests.get(f"{base}/configuration", params={"api_key": key}, timeout=10)
    except Exception as e:
        say(FAIL, "reach TMDb", str(e)[:200])
        return
    if resp.status_code == 200:
        say(PASS, "tmdb.api_key", "accepted")
    elif resp.status_code in (401, 403):
        say(FAIL, "tmdb.api_key", "refused - the key is wrong or has been revoked")
    else:
        say(FAIL, "reach TMDb", f"HTTP {resp.status_code}")


def check_assistant():
    """The model behind free-form requests. Optional."""
    heading("Assistant (optional - free-form requests)")
    try:
        from api import ai
    except Exception as e:
        say(SKIP, "AI provider", f"not available in this checkout: {str(e)[:120]}")
        return
    try:
        if not ai.has_api_key():
            say(SKIP, "AI provider", "no key set - requests fall back to keyword matching, "
                                     "which handles \"romcom\" but not a mood")
            return
        settings = ai.settings()
        if ai.provider() is None:
            say(FAIL, f"AI provider \"{settings.get('provider')}\"",
                "a key is set but the provider could not be built - check the name, "
                "and that its SDK is installed")
        else:
            say(PASS, f"AI provider \"{settings.get('provider')}\"",
                f"model {settings.get('model')}")
    except Exception as e:
        say(FAIL, "AI provider", str(e)[:200])


def check_ffprobe():
    """ffprobe reads what a file actually is. Only the quality audit needs it."""
    heading("ffprobe (optional - quality audit)")
    import shutil
    import subprocess

    from scanner.quality_auditor import FFPROBE_BIN

    binary = shutil.which(FFPROBE_BIN) or (FFPROBE_BIN if Path(FFPROBE_BIN).is_file() else None)
    if not binary:
        say(SKIP, "ffprobe", f"\"{FFPROBE_BIN}\" not found - install ffmpeg, or set "
                             "`ffprobe` in config.yaml to point at one")
        return
    try:
        out = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=10)
        version = (out.stdout or out.stderr).splitlines()[0]
    except Exception as e:
        say(FAIL, "run ffprobe", str(e)[:200])
        return
    say(PASS, binary, version)


def main():
    print("=" * 62)
    print("SETUP CHECK")
    print("=" * 62)

    config = check_config()
    if config is None:
        print("\nNothing else can be checked without a config.")
        return 1

    server = check_plex(config)
    check_libraries(server is not None)
    check_storage(config)
    check_tmdb(config)
    check_assistant()
    check_ffprobe()

    print("\n" + "=" * 62)
    if _failures:
        print(f"{_failures} problem{'s' if _failures > 1 else ''} to sort out. "
              "Skips are fine; fails are not.")
    else:
        print("All good. Start it with: venv/bin/python main.py")
    print("=" * 62)
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
