from pathlib import Path
from ffprobe import FFProbe
import requests
from config import config


class MediaAnalyzer:
    """Analyzes media files for quality info and fetches metadata from TMDb."""

    def __init__(self):
        # Absent settings are left blank rather than raised over: this class is
        # built on the way to routes that never touch TMDb, and a missing key
        # should stop those calls, not the whole app.
        tmdb = config.get("tmdb") or {}
        self.tmdb_api_key = tmdb.get("api_key") or ""
        self.tmdb_base_url = tmdb.get("base_url") or "https://api.themoviedb.org/3"

    def probe_file(self, file_path):
        """Run ffprobe on a media file and return stream info."""
        probe = FFProbe(str(file_path))
        result = {
            "file": str(file_path),
            "size_bytes": Path(file_path).stat().st_size,
            "video_streams": [],
            "audio_streams": [],
        }
        for stream in probe.streams:
            if stream.is_video():
                result["video_streams"].append({
                    "codec": stream.codec(),
                    "width": stream.width,
                    "height": stream.height,
                    "duration": stream.duration_seconds(),
                })
            elif stream.is_audio():
                result["audio_streams"].append({
                    "codec": stream.codec(),
                    "channels": stream.channels,
                    "sample_rate": stream.sample_rate,
                })
        return result

    def search_tmdb(self, query, media_type="movie"):
        """Search TMDb for a movie or TV show."""
        url = f"{self.tmdb_base_url}/search/{media_type}"
        params = {"api_key": self.tmdb_api_key, "query": query}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("results", [])

    def get_tmdb_details(self, tmdb_id, media_type="movie"):
        """Get detailed info for a specific TMDb entry."""
        url = f"{self.tmdb_base_url}/{media_type}/{tmdb_id}"
        params = {"api_key": self.tmdb_api_key}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _service(name):
        """The address and key of an optional companion service."""
        settings = config.get(name) or {}
        if not settings.get("url") or not settings.get("api_key"):
            raise RuntimeError(f"no {name} block in config.yaml")
        return settings["url"].rstrip("/"), settings["api_key"]

    def get_sonarr_series(self):
        """Fetch all series from Sonarr."""
        base, key = self._service("sonarr")
        resp = requests.get(f"{base}/api/v3/series", headers={"X-Api-Key": key}, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_radarr_movies(self):
        """Fetch all movies from Radarr."""
        base, key = self._service("radarr")
        resp = requests.get(f"{base}/api/v3/movie", headers={"X-Api-Key": key}, timeout=10)
        resp.raise_for_status()
        return resp.json()
