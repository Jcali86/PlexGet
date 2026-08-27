import os
from pathlib import Path
from plexapi.server import PlexServer
from config import config
from scanner import libraries


class MediaScanner:
    """Walks the configured library folders and Plex's own sections."""

    MEDIA_EXTENSIONS = {
        ".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".m4v",
        ".ts", ".webm", ".mpg", ".mpeg",
    }

    def __init__(self):
        self._plex = None

    @property
    def plex(self):
        """The Plex server, connected on the first question actually put to it.

        Walking the disks needs no Plex at all, so a scan has no business
        falling over because the server is off or the token has lapsed.
        """
        if self._plex is None:
            self._plex = PlexServer(config["plex"]["url"], config["plex"]["token"])
        return self._plex

    def _walk_path(self, base_path):
        """Walk a single directory and return all media files."""
        base = Path(base_path)
        if not base.exists():
            return []
        media_files = []
        for root, _dirs, files in os.walk(base):
            for f in files:
                if Path(f).suffix.lower() in self.MEDIA_EXTENSIONS:
                    media_files.append(Path(root) / f)
        return media_files

    def scan_nas_path(self, library_key):
        """Walk every folder one library is spread across."""
        media_files = []
        for path in libraries.paths_for(library_key):
            media_files.extend(self._walk_path(path))
        # Hook: track new files in poster_status for poster management
        self._poster_hook(library_key, media_files)
        return media_files

    def _poster_hook(self, library_key, file_paths):
        """After files are inserted into media_files, add missing entries to poster_status."""
        if not file_paths:
            return
        try:
            from scanner.poster_manager import hook_new_media
            from db import Database
            db = Database()
            added = hook_new_media(db, library_key, file_paths)
            if added:
                print(f"  [poster_hook] Added {added} new items to poster tracking")
            db.close()
        except Exception as e:
            # One line, not the whole of whatever came back: a server that
            # answers an error with a page of HTML should not bury the scan.
            print(f"  [poster_hook] Warning: {str(e).splitlines()[0][:120]}")

    def scan_single_path(self, raw_path):
        """Scan one specific directory path (for testing)."""
        return self._walk_path(raw_path)

    def scan_all_nas(self):
        """Scan every configured library."""
        return {key: self.scan_nas_path(key) for key in libraries.keys()}

    def is_manual_library(self, library_key):
        """True if this library is manually managed (no auto quality upgrades)."""
        return libraries.is_manual(library_key)

    def get_plex_libraries(self):
        """Return all Plex library sections."""
        return self.plex.library.sections()

    def get_plex_library_items(self, library_name):
        """Return all items in a specific Plex library."""
        return self.plex.library.section(library_name).all()

    def find_unmatched(self, library_key):
        """Files in a library's folders that Plex has not picked up."""
        section_name = libraries.section_name(library_key)
        if not section_name:
            return set()
        plex_paths = set()
        for item in self.get_plex_library_items(section_name):
            for media in item.media:
                for part in media.parts:
                    plex_paths.add(part.file)

        on_disk = set(str(f) for f in self.scan_nas_path(library_key))
        return on_disk - plex_paths
