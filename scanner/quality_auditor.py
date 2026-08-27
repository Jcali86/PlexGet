import json
import re
import shutil
import subprocess
from pathlib import Path
from config import config
from db import Database
from scanner.libraries import keys_of_kind

# ffprobe reads what a file actually is. Whatever is on PATH is normally the
# right answer; name one in config when it is somewhere unusual, or when the
# only copy on the machine is the one bundled inside another app.
FFPROBE_BIN = config.get("ffprobe") or shutil.which("ffprobe") or "ffprobe"

# Starting score — deductions bring it down
MAX_SCORE = 100

# ---------------------------------------------------------------------------
# Codec / container classifications
# ---------------------------------------------------------------------------

OLD_VIDEO_CODECS = {
    "mpeg2video",  # MPEG-2
    "mpeg4",       # MPEG-4 Part 2 (DivX/XviD)
    "msmpeg4v3",   # MS-MPEG4 v3
    "msmpeg4v2",
    "msmpeg4v1",
    "wmv1", "wmv2", "wmv3",
}

# codec_tag_string values that indicate early/low-quality x264
EARLY_X264_TAGS = {"XVID", "DIVX", "DX50", "DIV3", "MP43"}

BAD_CONTAINERS = {
    "avi": "AVI — legacy container, no modern codec support",
    "wmv": "WMV — proprietary, poor compatibility",
    "asf": "ASF — legacy Windows Media container",
    "flv": "FLV — Flash container, outdated",
    "mpegts": "MPEG-TS — transport stream, not ideal for storage",
}

PREFERRED_CONTAINERS = {"matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2"}

PRE_2000_CUTOFF = 2000

_YEAR_PATTERN = re.compile(r"[\.\s\(](\d{4})[\.\s\)\-]")


def _parse_year_from_path(file_path):
    """Extract the earliest plausible year (1950-2030) from a file path."""
    years = [int(y) for y in _YEAR_PATTERN.findall(file_path) if 1950 <= int(y) <= 2030]
    return min(years) if years else None


# ---------------------------------------------------------------------------
# ffprobe wrapper
# ---------------------------------------------------------------------------

def probe_file(file_path):
    """Run ffprobe and return parsed JSON with streams + format."""
    try:
        result = subprocess.run(
            [
                FFPROBE_BIN, "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(file_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def _extract_info(probe_data):
    """Extract relevant fields from ffprobe JSON."""
    fmt = probe_data.get("format", {})
    streams = probe_data.get("streams", [])

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    # Video bitrate: prefer stream-level, fall back to format-level
    video_bitrate = None
    if video:
        video_bitrate = _int_or_none(video.get("bit_rate"))
    if not video_bitrate:
        # Estimate: total bitrate minus audio bitrate
        total_br = _int_or_none(fmt.get("bit_rate"))
        audio_br = _int_or_none(audio.get("bit_rate")) if audio else 0
        if total_br:
            video_bitrate = total_br - (audio_br or 0)

    info = {
        "container": fmt.get("format_name"),
        "duration_seconds": _float_or_none(fmt.get("duration")),
        "video_codec": video.get("codec_name") if video else None,
        "video_codec_tag": video.get("codec_tag_string") if video else None,
        "video_bitrate": video_bitrate,
        "width": _int_or_none(video.get("width")) if video else None,
        "height": _int_or_none(video.get("height")) if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_channels": _int_or_none(audio.get("channels")) if audio else None,
        "audio_bitrate": _int_or_none(audio.get("bit_rate")) if audio else None,
    }

    if info["width"] and info["height"]:
        info["resolution"] = f"{info['width']}x{info['height']}"
    else:
        info["resolution"] = None

    return info


def _int_or_none(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _float_or_none(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

def score_file(info):
    """Score a file 0-100 and return (score, issues, recommendation).

    Deductions are cumulative. Multiple issues stack.
    """
    score = MAX_SCORE
    issues = []
    recs = []

    height = info.get("height") or 0
    video_bitrate = info.get("video_bitrate") or 0
    video_codec = (info.get("video_codec") or "").lower()
    codec_tag = (info.get("video_codec_tag") or "").upper()
    audio_channels = info.get("audio_channels") or 0
    container = (info.get("container") or "").lower()

    # --- Video codec issues ---
    if video_codec in OLD_VIDEO_CODECS:
        score -= 30
        issues.append(f"old_video_codec:{video_codec}")
        recs.append(f"Re-encode from {video_codec} to H.265/HEVC or AV1")

    if codec_tag in EARLY_X264_TAGS:
        score -= 25
        issues.append(f"legacy_codec_tag:{codec_tag}")
        recs.append(f"Legacy encoder tag {codec_tag} — re-encode with modern x264/x265")

    # --- Bitrate issues ---
    bitrate_mbps = video_bitrate / 1_000_000 if video_bitrate else 0

    if height >= 1080 and 0 < bitrate_mbps < 3.0:
        score -= 20
        issues.append(f"low_bitrate_1080p:{bitrate_mbps:.1f}Mbps")
        recs.append(f"1080p at {bitrate_mbps:.1f} Mbps is starved — find a higher-bitrate source")
    elif height >= 720 and height < 1080 and 0 < bitrate_mbps < 1.5:
        score -= 15
        issues.append(f"low_bitrate_720p:{bitrate_mbps:.1f}Mbps")
        recs.append(f"720p at {bitrate_mbps:.1f} Mbps is starved — find a higher-bitrate source")

    # --- Audio issues ---
    if audio_channels <= 2:
        score -= 10
        issues.append(f"stereo_only:{audio_channels}ch")
        recs.append("Stereo only — look for a 5.1/7.1 surround release")

    # --- Container issues ---
    for bad_fmt, desc in BAD_CONTAINERS.items():
        if bad_fmt in container:
            score -= 15
            issues.append(f"bad_container:{bad_fmt}")
            recs.append(f"{desc} — remux to MKV")
            break

    # --- Resolution bonus/penalty ---
    if height < 720 and height > 0:
        score -= 10
        issues.append(f"low_resolution:{info.get('resolution', 'unknown')}")
        recs.append("Sub-720p — upgrade to at least 720p if available")

    score = max(0, score)

    return score, "; ".join(issues) if issues else None, " | ".join(recs) if recs else None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_quality_audit(db, limit=None, libraries=None):
    """Audit files in non-manual libraries.

    limit: if set, only audit this many files (for testing).
    libraries: if set, only audit these specific libraries.
    """
    if libraries is None:
        libraries = db.get_non_manual_libraries()
    tv_libraries = set(keys_of_kind("show"))
    total_audited = 0
    total_issues = 0
    skipped = 0

    for lib in libraries:
        rows = db.get_all_media(lib)
        print(f"  [{lib}] {len(rows)} files to audit")

        for i, row in enumerate(rows):
            if limit and total_audited >= limit:
                break

            file_path = row["file_path"]
            if not Path(file_path).exists():
                continue

            # Skip pre-2000 TV shows — older content where low quality is expected
            if lib in tv_libraries:
                year = _parse_year_from_path(file_path)
                if year is not None and year < PRE_2000_CUTOFF:
                    skipped += 1
                    continue

            probe_data = probe_file(file_path)
            if not probe_data:
                print(f"    SKIP (ffprobe failed): {Path(file_path).name}")
                continue

            info = _extract_info(probe_data)
            score, issues, recommendation = score_file(info)

            db.upsert_quality(
                file_path,
                library=lib,
                container=info["container"],
                video_codec=info["video_codec"],
                video_codec_tag=info["video_codec_tag"],
                video_bitrate=info["video_bitrate"],
                resolution=info["resolution"],
                width=info["width"],
                height=info["height"],
                audio_codec=info["audio_codec"],
                audio_channels=info["audio_channels"],
                audio_bitrate=info["audio_bitrate"],
                duration_seconds=info["duration_seconds"],
                score=score,
                issues=issues,
                upgrade_recommendation=recommendation,
            )

            total_audited += 1
            if issues:
                total_issues += 1
                print(f"    [{score:3d}] {Path(file_path).name}")
                print(f"          Issues: {issues}")

        if limit and total_audited >= limit:
            break

    return {
        "files_audited": total_audited,
        "files_with_issues": total_issues,
        "skipped_pre2000": skipped,
    }
