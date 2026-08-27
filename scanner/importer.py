"""File finished downloads into the Plex libraries.

This moves media you already have into the right library folder, under the
naming Plex expects, then tells Plex to look at just that folder and closes the
matching wanted-list entry. It fetches nothing - you supply the files.

Nothing is deleted. When a staging item turns out to be on the NAS already the
tool says so and leaves both copies alone; reclaiming the space stays your call.
"""

import os
import re
import shutil
import subprocess

from scanner import libraries
from scanner.wanted_search import parse_query, search_plex

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".wmv"}
JUNK = re.compile(r"(sample|trailer|rarbg|\.nfo$|screens?)", re.I)

# Release-name noise, stripped to recover the title.
NOISE = re.compile(
    r"\b(1080p|2160p|720p|480p|4k|uhd|bluray|blu-ray|brrip|bdrip|web-?dl|web-?rip|hdtv|"
    r"remux|remastered|repack|proper|extended|unrated|imax|dovi|hdr10|hdr|10bit|8bit|"
    r"x264|x265|h\.?264|h\.?265|hevc|avc|aac|ac3|dts(-hd)?|ddp?5|atmos|truehd|amzn|nf|dsnp|ma|nordic|multi|dual|subbed|dubbed|complete|season)\b.*",
    re.I,
)
EPISODE = re.compile(r"\bS(\d{1,2})[\s._-]?E(\d{1,3})\b", re.I)


def clean_title(raw):
    """Recover a title from a release folder or file name."""
    name = re.sub(r"\.(mkv|mp4|avi|m4v|ts|mov|wmv)$", "", raw, flags=re.I)
    name = NOISE.sub("", name)
    name = name.replace(".", " ").replace("_", " ")
    name = re.sub(r"[\[\(].*?[\]\)]", " ", name)
    name = re.sub(r"[-–]\s*[A-Za-z0-9]+$", "", name)   # trailing release group
    return re.sub(r"\s{2,}", " ", name).strip(" -_.")


def main_video(path):
    """The feature file in a download folder: the largest non-sample video."""
    if os.path.isfile(path):
        return path if os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS else None
    best, best_size = None, 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTENSIONS or JUNK.search(name):
                continue
            full = os.path.join(root, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > best_size:
                best, best_size = full, size
    return best


def inspect(path):
    """Work out what a staging item is: title, year, and whether it is an episode."""
    base = os.path.basename(path.rstrip("/"))
    video = main_video(path)
    item = {
        "source": path,
        "name": base,
        "video": video,
        "size": os.path.getsize(video) if video else 0,
        "kind": "unknown",
        "title": "",
        "year": None,
        "season": None,
        "episode": None,
    }
    if not video:
        archived = False
        if os.path.isdir(path):
            for _root, _dirs, files in os.walk(path):
                if any(re.search(r"\.(rar|r\d{2}|zip|7z|part\d+\.rar)$", f, re.I) for f in files):
                    archived = True
                    break
        item["problem"] = "archive not extracted" if archived else "no video file found"
        return item

    episode = EPISODE.search(base) or EPISODE.search(os.path.basename(video))
    if episode:
        item["kind"] = "episode"
        item["season"] = int(episode.group(1))
        item["episode"] = int(episode.group(2))
        show = base[: episode.start()] if EPISODE.search(base) else base
        item["title"] = clean_title(show) or clean_title(base)
        year = re.search(r"\b(19|20)\d{2}\b", item["title"])
        if year:  # "Ludwig 2024" - the year belongs to the show, not the title
            item["year"] = int(year.group(0))
            item["title"] = item["title"][: year.start()].strip()
    else:
        item["kind"] = "movie"
        # A bracketed year - "Class.Action.Park.(2020).1080p" - has to be read
        # before the brackets are stripped as release noise.
        bracketed = re.search(r"[\(\[]((?:19|20)\d{2})[\)\]]", base)
        title, year = parse_query(clean_title(base))
        if not year and bracketed:
            year = int(bracketed.group(1))
        item["title"], item["year"] = title, year
    return item


def plex_state(item):
    """Is this already in Plex, and can Plex actually play it?"""
    if item["kind"] != "movie" or not item["title"]:
        return {"in_plex": False, "matches": []}
    matches = search_plex(item["title"])
    if item["year"]:
        exact = [m for m in matches if m["year"] == item["year"]]
        matches = exact or matches
    playable = [m for m in matches if not m["file_missing"]]
    return {"in_plex": bool(playable), "matches": matches}


def library_paths(library_key):
    return libraries.paths_for(library_key)


def free_bytes(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


SEASON_IN_NAME = re.compile(r"\bS(?:eason)?\.?\s?(\d{1,2})\b", re.I)


def existing_show_folder(library_key, show_title, season=None):
    """Where a show already lives, so episodes join it rather than start a rival folder.

    Library folders are often named after a release - "Ted.Lasso.S01.1080p.ATVP
    .WEB-DL..." - so an exact name match finds nothing. Matching on the
    normalised prefix does. A folder that names a *different* season is left
    alone: dropping season 4 inside a season 1 release folder would be worse
    than making a clean folder.
    """
    target = re.sub(r"[^a-z0-9]", "", show_title.lower())
    if not target:
        return None
    best = None
    for base in library_paths(library_key):
        if not os.path.isdir(base):
            continue
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in entries:
            normalised = re.sub(r"[^a-z0-9]", "", entry.lower())
            if normalised != target and not normalised.startswith(target):
                continue
            marked = SEASON_IN_NAME.search(entry)
            if marked and season is not None and int(marked.group(1)) != season:
                continue
            candidate = os.path.join(base, entry)
            # Prefer the tidiest name: closest to the bare show title.
            if best is None or len(entry) < len(os.path.basename(best)):
                best = candidate
    return best


def choose_destination(item, library_key, headroom_gb=20):
    """Pick where this should go: an existing show folder, else the emptiest volume."""
    if item["kind"] == "episode":
        show_dir = existing_show_folder(library_key, item["title"], item["season"])
        if show_dir:
            return os.path.join(show_dir, f"Season {item['season']:02d}"), "existing show folder"

    candidates = [(free_bytes(p), p) for p in library_paths(library_key) if os.path.isdir(p)]
    candidates = [(free, p) for free, p in candidates
                  if free > item["size"] + headroom_gb * 1024**3]
    if not candidates:
        return None, "no volume has room"
    free, base = max(candidates)
    if item["kind"] == "episode":
        folder = os.path.join(base, item["title"], f"Season {item['season']:02d}")
    else:
        name = f"{item['title']} ({item['year']})" if item["year"] else item["title"]
        folder = os.path.join(base, name)
    return folder, f"{free / 1024**3:.0f} GB free"


def target_filename(item):
    ext = os.path.splitext(item["video"])[1].lower()
    if item["kind"] == "episode":
        return f"{item['title']} - S{item['season']:02d}E{item['episode']:02d}{ext}"
    return (f"{item['title']} ({item['year']}){ext}" if item["year"]
            else f"{item['title']}{ext}")


def copy_file(source, destination):
    """Copy one file to the NAS, verifying the size afterwards.

    macOS ships openrsync, which accepts almost no long options - no
    --partial, --exclude or --info. `-aP` (archive, partial, progress) is
    supported and gives resumable copies; if rsync is missing or refuses,
    fall back to a plain copy.
    """
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    try:
        result = subprocess.run(
            ["rsync", "-aP", source, destination], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[:200] or "rsync failed")
    except (OSError, RuntimeError) as e:
        try:
            shutil.copy2(source, destination)
        except OSError as copy_error:
            return False, f"{e}; copy also failed: {copy_error}"
    try:
        if os.path.getsize(source) != os.path.getsize(destination):
            return False, "size mismatch after copy"
    except OSError as e:
        return False, str(e)
    return True, ""


def notify_plex(library_key, folder):
    """Ask Plex to look at just this folder rather than rescan the library.

    Which section owns the folder is worked out from the folders Plex itself
    was pointed at, so nothing here has to know what anybody calls a library.
    """
    from scanner.wanted_search import get_server
    section_name = libraries.section_name(library_key)
    if not section_name:
        return False, f"no Plex section found for {library_key}"
    try:
        get_server().library.section(section_name).update(path=folder)
        return True, section_name
    except Exception as e:
        return False, str(e)[:160]


def close_wanted(db, item):
    """Mark the matching wanted entry acquired and tell whoever asked for it."""
    if item["kind"] != "movie":
        return None
    row = db.find_wanted(item["title"], item["year"]) or db.find_wanted(item["title"], None)
    if row is None or row["status"] == "acquired":
        return None
    db.set_wanted_status(row["id"], "acquired")

    columns = row.keys()
    requester = row["requested_by_id"] if "requested_by_id" in columns else None
    if requester:
        try:
            from api import push as web_push

            label = row["title"] + (f" ({row['year']})" if row["year"] else "")
            web_push.send_event(
                db, "arrived", requester, url="/", tag=f"arrived-{row['id']}", film=label
            )
        except Exception:
            pass   # a notification failing must never fail an import
    return row["title"]
