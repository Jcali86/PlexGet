"""Where the files actually are, from where this app is standing.

Plex reports the path it uses, which is not always the path this app can read.
Plex in a container sees /data/media where the machine running this sees
/mnt/media. Two accounts signed into the same Mac means the second one is
handed "/Volumes/Media-1" while Plex still says "/Volumes/Media". A Plex on
Windows says D:\\Media to something reading over a share. Rather than insist
the mounts be tidy, every place media lives is named in config, along with any
other name it can turn up under, and whichever name answers is the one used.

The other half of the job is telling an outage from a deletion. A file on a
store that cannot be read is not missing, it is unknown, and saying otherwise
would move an entire library onto the wanted list in one go.
"""

import os
import re
import threading
import time

from config import config

# Paths arrive from Plex written the way Plex's own machine writes them, which
# is not necessarily the way this one does, so both separators are honoured on
# both sides of every comparison.
SEPARATORS = ("/", "\\")
_SPLIT = re.compile(r"[\\/]+")

# Seconds a directory listing may take before the store is called dead, and
# seconds a verdict about a store is reused for. Both are settable in config;
# these are what you get without.
DEFAULT_PROBE_TIMEOUT = 5
DEFAULT_PROBE_CACHE = 30

_stores = None
_library_roots = None
_state = {}


def _settings():
    return config.get("paths") or {}


def probe_timeout():
    try:
        return float(_settings().get("probe_timeout") or DEFAULT_PROBE_TIMEOUT)
    except (TypeError, ValueError):
        return DEFAULT_PROBE_TIMEOUT


def probe_cache():
    try:
        return float(_settings().get("probe_cache") or DEFAULT_PROBE_CACHE)
    except (TypeError, ValueError):
        return DEFAULT_PROBE_CACHE


def trim(path):
    """A path with any trailing separator taken off, and nothing else touched."""
    text = (path or "").strip()
    while len(text) > 1 and text[-1] in SEPARATORS:
        text = text[:-1]
    return text


def is_under(path, root):
    """Is this path the root itself, or something inside it?"""
    path, root = trim(path), trim(root)
    if not path or not root:
        return False
    if path == root:
        return True
    return any(path.startswith(root + sep) for sep in SEPARATORS)


def _pieces(path, root):
    """The parts of a path below a root, as a list of folder names."""
    return [piece for piece in _SPLIT.split(path[len(root):]) if piece]


def _names(entry):
    """A store's names, from either shape config allows.

    A bare string is a store with one name; a list is the same store under
    several, the first being the one Plex reports.
    """
    if isinstance(entry, str):
        entry = [entry]
    names = [trim(name) for name in (entry or []) if isinstance(name, str) and name.strip()]
    return tuple(dict.fromkeys(names))


def library_paths():
    """Every library folder named in config, flattened.

    Each key under `nas` is a library and holds either one path or a list of
    them, one per drive it is spread across.
    """
    found = []
    for paths in (config.get("nas") or {}).values():
        if isinstance(paths, str):
            paths = [paths]
        for path in paths or []:
            if isinstance(path, str) and path.strip():
                found.append(trim(path))
    return found


def stores():
    """Every store, each as the names it answers to, deepest first.

    Deepest first so a store nested inside another - a 4K folder on a drive
    that is itself a store - is the one a path inside it matches.
    """
    global _stores
    if _stores is None:
        configured = [_names(entry) for entry in (_settings().get("roots") or [])]
        configured = [names for names in configured if names]
        # Nothing configured is the ordinary case, not an error: when Plex and
        # this app agree about where the files are, the library folders already
        # in config are the stores, and there is nothing to translate.
        if not configured:
            configured = [(path,) for path in dict.fromkeys(library_paths())]
        _stores = sorted(configured, key=lambda names: -len(names[0]))
    return _stores


def store_for(path):
    """The store a path sits on, or None when it sits on none of them."""
    target = trim(path)
    if not target:
        return None
    for names in stores():
        if is_under(target, names[0]):
            return names
    return None


def library_roots():
    """Every library folder, deepest first, for reading structure out of a path."""
    global _library_roots
    if _library_roots is None:
        _library_roots = sorted(dict.fromkeys(library_paths()), key=len, reverse=True)
    return _library_roots


def library_root_for(file_path):
    """The library folder a file sits in, or None when it is outside them all."""
    target = trim(file_path)
    if not target:
        return None
    for root in library_roots():
        if is_under(target, root):
            return root
    return None


def below_library(file_path):
    """The folder names between a file's library and the file itself.

    This is how a show is picked out of a path without knowing what anybody
    calls their folders: the first name below the library folder is the show,
    whatever the library is named and wherever it lives.
    """
    root = library_root_for(file_path)
    if root is None:
        return []
    return _pieces(trim(file_path), root)


def readable_path(path):
    """Where this file is, as this app can reach it.

    The path Plex names is tried first and kept when it answers. Only when it
    does not are the store's other names tried in turn, so an unreadable
    original is not mistaken for a deleted file.
    """
    target = trim(path)
    if not target or os.path.exists(target):
        return path
    names = store_for(target)
    if not names:
        return path
    rest = _pieces(target, names[0])
    for name in names[1:]:
        candidate = os.path.join(name, *rest) if rest else name
        if os.path.exists(candidate):
            return candidate
    return path


def _listable(path, patience):
    """Can this folder be read, and does it hold anything, answered in time?

    A wedged network mount does not refuse - it hangs, indefinitely, taking the
    request with it. The listing runs in a thread this is prepared to abandon:
    an answer that does not arrive inside `patience` is treated as no.

    Whether it is a mount is not the question, and asking that was the mistake
    behind a real incident: two accounts signed into the same Mac, the plain
    name held the whole time by the other one, so every read came back
    "Operation not permitted" while ismount cheerfully said True. Trusting
    ismount made every file on a present-but-unreadable drive look individually
    deleted. Only a folder that answers a real listing counts as present.

    Empty counts as no as well, for the other half of the same problem: a drive
    that has gone away often leaves the folder it was mounted at sitting there,
    readable and empty, and a store answering with nothing cannot be told from
    one that is not there. Unknown is the safe reading of that; present would
    report every file on it deleted.
    """
    outcome = []

    def probe():
        try:
            outcome.append(bool(os.listdir(path)))
        except OSError:
            outcome.append(False)

    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(patience)
    return bool(outcome) and outcome[0]


def store_ready(name):
    """Is this name a store that can be read right now?

    Remembered briefly: availability() runs once per film across whole result
    sets, and a directory listing over a network share per film would turn one
    search into hundreds.
    """
    now = time.time()
    cached = _state.get(name)
    if cached and now - cached[0] < probe_cache():
        return cached[1]
    ready = _listable(name, probe_timeout())
    _state[name] = (now, ready)
    return ready


def store_offline(path):
    """Is the store this file lives on simply not usable right now?

    A file is only meaningfully "missing" if the store under it is live and
    readable. When a drive drops, or comes back under another name, or is held
    by another account so every read is refused, every path under it reads as
    absent. That is an outage of one store, not thousands of individual
    deletions, and must never be mistaken for one: it would empty every search
    and, worse, move the whole library onto the wanted list and fire a
    missing-file alert for each title.

    The store counts as present if ANY of its names answers - the one Plex
    reports, or one of the stand-ins. Only when none of them answers is this
    genuinely unknown; a file absent from a store that does answer is genuinely
    absent. A path on no known store is nobody's outage, so it is judged on
    whether the file is there.
    """
    names = store_for(path)
    if not names:
        return False
    return not any(store_ready(name) for name in names)


def disk_label(path):
    """Which disk a file sits on, as a name to group scans by.

    Read off the filesystem rather than out of the path text. The rule here
    used to be the folder under /Volumes, which is true on a Mac and true
    nowhere else, so an install keeping its libraries under /mnt or /srv
    recorded no disk at all for any of them. Walking up to the mount point
    gives the same answer where the old rule worked and a real one everywhere
    else; on a host with a single disk everything lands under the same name,
    which is honest, since it is all one disk.
    """
    try:
        here = os.path.realpath(path)
    except OSError:
        return None
    while True:
        if os.path.ismount(here):
            return os.path.basename(here) or here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def forget():
    """Drop what is remembered about the stores, so the next question is asked afresh."""
    _state.clear()


def status():
    """Every store and the name of it that answers, for a setup check."""
    return [
        {
            "names": list(names),
            "reachable": next((name for name in names if store_ready(name)), None),
        }
        for names in stores()
    ]
