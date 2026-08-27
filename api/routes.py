from pathlib import Path

import json
import os
import re
import time

import requests

from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from scanner import MediaScanner
from scanner.gap_analysis import run_full_analysis, run_tv_analysis, run_movie_analysis
from scanner.quality_auditor import run_quality_audit
from scanner.reports import generate_upgrade_report, generate_gap_report, generate_poster_report
from scanner.plex_fixer import run_audit, apply_rename, apply_batch, get_known_issues
from api.nl_controller import execute_command
from scanner.poster_manager import (
    scan_poster_status, apply_tmdb_poster, apply_tmdb_bulk,
    import_mediux_set, enforce_locks, detect_orphans, get_poster_status,
)
from scanner.export_builder import generate_export, generate_export_text
from api import limits as ai_limits
from api import persona
from api import plex_auth
from api import suggestions as suggest
from api import tmdb_lookup
from api import push as web_push
from api import user_library
from api.ai import has_api_key
from api.request_assistant import (
    close_matches,
    off_topic,
    search as mood_search,
    search_by_person,
    search_by_title,
    showcase,
    worth_asking,
)
from scanner.wanted_search import parse_query, query_hint
from scanner.wanted_search import (
    PlexTokenDead,
    PlexUnavailable,
    export_text as wanted_export_text,
    recheck as recheck_wanted,
    search_and_capture,
)
from analyzer import MediaAnalyzer
from config import config
from db import Database


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
        here = Path(path).resolve()
    except OSError:
        return None
    for candidate in [here, *here.parents]:
        if os.path.ismount(candidate):
            return candidate.name or str(candidate)
    return None


def create_app():
    app = Flask(__name__)
    # Whatever puts https in front of this - a tunnel, a reverse proxy, a
    # hosting front end - terminates TLS and forwards on over plain HTTP, so
    # without this the app believes every request is http:// - and a preview
    # image advertised as http:// on an https:// page is blocked as mixed
    # content, which is why a shared link kept showing whatever icon the phone
    # had cached. One hop only: anything written further out than the proxy
    # immediately in front is the client's own words.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

    @app.route("/dashboard")
    def dashboard():
        return send_from_directory(str(DASHBOARD_DIR), "index.html")

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/summary")
    def summary():
        db = Database()
        total_files = db.conn.execute("SELECT COUNT(*) FROM quality_scores").fetchone()[0]
        avg_score = db.conn.execute("SELECT ROUND(AVG(score),1) FROM quality_scores").fetchone()[0]
        tv_gaps = db.conn.execute(
            "SELECT COUNT(*) FROM gaps WHERE gap_type IN ('missing_season','missing_episode')"
        ).fetchone()[0]
        franchise_gaps = db.conn.execute(
            "SELECT COUNT(*) FROM gaps WHERE gap_type = 'missing_franchise_entry'"
        ).fetchone()[0]
        alt_versions = db.conn.execute(
            "SELECT COUNT(*) FROM gaps WHERE gap_type = 'alternate_version_available'"
        ).fetchone()[0]
        files_with_issues = db.conn.execute(
            "SELECT COUNT(*) FROM quality_scores WHERE issues IS NOT NULL"
        ).fetchone()[0]

        by_library = [dict(r) for r in db.conn.execute("""
            SELECT library,
                   COUNT(*) as total,
                   COUNT(CASE WHEN issues IS NOT NULL THEN 1 END) as with_issues,
                   ROUND(AVG(score),1) as avg_score,
                   MIN(score) as worst_score
            FROM quality_scores GROUP BY library ORDER BY total DESC
        """).fetchall()]

        distribution = [dict(r) for r in db.conn.execute("""
            SELECT
                CASE
                    WHEN score <= 20 THEN '0-20'
                    WHEN score <= 40 THEN '21-40'
                    WHEN score <= 60 THEN '41-60'
                    WHEN score <= 70 THEN '61-70'
                    WHEN score <= 80 THEN '71-80'
                    WHEN score <= 90 THEN '81-90'
                    WHEN score <= 100 THEN '91-100'
                END as range,
                COUNT(*) as count
            FROM quality_scores GROUP BY range ORDER BY range
        """).fetchall()]

        db.close()
        return jsonify({
            "total_files": total_files,
            "avg_score": avg_score,
            "files_with_issues": files_with_issues,
            "tv_gaps": tv_gaps,
            "franchise_gaps": franchise_gaps,
            "alt_versions": alt_versions,
            "by_library": by_library,
            "distribution": distribution,
        })

    @app.route("/libraries")
    def libraries():
        scanner = MediaScanner()
        sections = scanner.get_plex_libraries()
        return jsonify([{"title": s.title, "type": s.type} for s in sections])

    @app.route("/scan/<library>", methods=["POST"])
    def scan_library(library):
        scanner = MediaScanner()
        files = scanner.scan_nas_path(library)
        manual = scanner.is_manual_library(library)
        db = Database()
        # Worked out per folder rather than per file: every file in one folder
        # is on the same disk, and a library of ten thousand would otherwise
        # ask the filesystem the same question ten thousand times.
        by_folder = {}
        for f in files:
            if f.parent not in by_folder:
                by_folder[f.parent] = disk_label(f.parent)
            volume = by_folder[f.parent]
            db.upsert_media(
                str(f),
                library=library,
                volume=volume,
                size_bytes=f.stat().st_size,
                manual_only=manual,
            )
        db.log_scan(f"nas_{library}", len(files))
        db.close()
        return jsonify({"library": library, "scanned": len(files), "manual_only": manual})

    @app.route("/scan/test", methods=["POST"])
    def scan_test():
        """Scan a single specific path for validation."""
        path = request.json.get("path")
        if not path:
            return jsonify({"error": "path required"}), 400
        scanner = MediaScanner()
        files = scanner.scan_single_path(path)
        return jsonify({
            "path": path,
            "files_found": len(files),
            "files": [str(f) for f in files[:50]],
            "truncated": len(files) > 50,
        })

    @app.route("/analyze", methods=["POST"])
    def analyze_file():
        file_path = request.json.get("file_path")
        if not file_path:
            return jsonify({"error": "file_path required"}), 400
        analyzer = MediaAnalyzer()
        info = analyzer.probe_file(file_path)
        db = Database()
        db.upsert_media(
            file_path,
            video_codec=info["video_streams"][0]["codec"] if info["video_streams"] else None,
            resolution=f"{info['video_streams'][0]['width']}x{info['video_streams'][0]['height']}" if info["video_streams"] else None,
            audio_codec=info["audio_streams"][0]["codec"] if info["audio_streams"] else None,
            size_bytes=info["size_bytes"],
        )
        db.close()
        return jsonify(info)

    @app.route("/search/tmdb")
    def search_tmdb():
        query = request.args.get("q", "")
        media_type = request.args.get("type", "movie")
        analyzer = MediaAnalyzer()
        results = analyzer.search_tmdb(query, media_type)
        return jsonify(results)

    @app.route("/media")
    def list_media():
        library = request.args.get("library")
        db = Database()
        rows = db.get_all_media(library)
        db.close()
        return jsonify([dict(row) for row in rows])

    @app.route("/analyze/gaps", methods=["POST"])
    def analyze_gaps():
        """Run gap analysis. Optional JSON body: {"limit": N, "type": "tv"|"movies"}."""
        body = request.get_json(silent=True) or {}
        limit = body.get("limit")
        analysis_type = body.get("type")
        db = Database()
        if analysis_type == "tv":
            result = run_tv_analysis(db, limit=limit)
        elif analysis_type == "movies":
            result = run_movie_analysis(db, limit=limit)
        else:
            result = run_full_analysis(db, limit=limit)
        db.close()
        return jsonify(result)

    @app.route("/gaps")
    def get_gaps():
        """Query gaps. Optional params: type, library."""
        gap_type = request.args.get("type")
        library = request.args.get("library")
        db = Database()
        rows = db.get_gaps(gap_type=gap_type, library=library)
        db.close()
        return jsonify([dict(row) for row in rows])

    @app.route("/analyze/quality", methods=["POST"])
    def analyze_quality():
        """Run quality audit. Optional JSON body: {"limit": N}."""
        body = request.get_json(silent=True) or {}
        limit = body.get("limit")
        db = Database()
        result = run_quality_audit(db, limit=limit)
        db.close()
        return jsonify(result)

    @app.route("/quality")
    def get_quality():
        """Query quality scores. Params: min_score, max_score, issue, library."""
        min_score = request.args.get("min_score", type=int)
        max_score = request.args.get("max_score", type=int)
        issue = request.args.get("issue")
        library = request.args.get("library")
        db = Database()
        rows = db.get_quality(
            min_score=min_score, max_score=max_score,
            issue=issue, library=library,
        )
        db.close()
        return jsonify([dict(row) for row in rows])

    @app.route("/reports/upgrades")
    def upgrade_report():
        """Prioritized upgrade report. Params: min_score, max_score (default 70), library, limit, issue."""
        max_score = request.args.get("max_score", 70, type=int)
        min_score = request.args.get("min_score", type=int)
        library = request.args.get("library")
        limit = request.args.get("limit", 500, type=int)
        issue = request.args.get("issue")
        report = generate_upgrade_report(
            max_score=max_score, min_score=min_score,
            library=library, limit=limit, issue=issue,
        )
        return jsonify(report)

    @app.route("/reports/gaps")
    def gap_report():
        """Gap report. Params: type (tv|franchise|alternate), library, limit."""
        gap_type = request.args.get("type")
        library = request.args.get("library")
        limit = request.args.get("limit", 500, type=int)
        report = generate_gap_report(
            gap_type=gap_type, library=library, limit=limit,
        )
        return jsonify(report)

    @app.route("/reports/posters")
    def poster_report():
        """Poster coverage report. Params: library."""
        library = request.args.get("library")
        report = generate_poster_report(library=library)
        return jsonify(report)

    @app.route("/fixer/audit", methods=["POST"])
    def fixer_audit():
        """Run Plex naming audit. Optional JSON body: {"limit": N}."""
        body = request.get_json(silent=True) or {}
        limit = body.get("limit")
        db = Database()
        result = run_audit(db, limit=limit)
        db.close()
        return jsonify(result)

    @app.route("/fixer/suggestions")
    def fixer_suggestions():
        """Query rename suggestions. Params: issue, confidence, library."""
        issue = request.args.get("issue")
        confidence = request.args.get("confidence")
        library = request.args.get("library")
        db = Database()
        rows = db.get_suggestions(
            issue_type=issue, confidence=confidence, library=library,
        )
        db.close()
        return jsonify([dict(row) for row in rows])

    @app.route("/fixer/apply", methods=["POST"])
    def fixer_apply():
        """Apply rename suggestions by ID. JSON body: {"ids": [1, 2, 3]}."""
        body = request.get_json(silent=True) or {}
        ids = body.get("ids", [])
        if not ids:
            return jsonify({"error": "ids list required"}), 400
        db = Database()
        results = []
        for sid in ids:
            success, msg = apply_rename(db, sid)
            results.append({"id": sid, "success": success, "message": msg})
        db.close()
        return jsonify({"results": results})

    @app.route("/fixer/apply-all", methods=["POST"])
    def fixer_apply_all():
        """Batch apply suggestions. Params: confidence (default: high)."""
        confidence = request.args.get("confidence", "high")
        db = Database()
        result = apply_batch(db, confidence=confidence)
        db.close()
        return jsonify(result)

    @app.route("/fixer/known-issues")
    def fixer_known_issues():
        """Return known problem shows and their fixes."""
        return jsonify(get_known_issues())

    @app.route("/command", methods=["POST"])
    def nl_command():
        """Natural language command endpoint."""
        body = request.get_json(silent=True) or {}
        command = body.get("command", "").strip()
        if not command:
            return jsonify({"error": "command required", "example": {"command": "what's missing from Breaking Bad"}}), 400
        result = execute_command(command)
        return jsonify(result)

    # ── Poster management endpoints ────────────────────────────────────────

    @app.route("/posters/status")
    def poster_status():
        """Query poster status. Params: library, state, limit."""
        library = request.args.get("library")
        state = request.args.get("state")
        limit = request.args.get("limit", 500, type=int)
        db = Database()
        rows = get_poster_status(db, library=library, state=state, limit=limit)
        db.close()
        return jsonify([dict(r) for r in rows])

    @app.route("/posters/scan", methods=["POST"])
    def poster_scan():
        """Populate/refresh poster_status from Plex."""
        body = request.get_json(silent=True) or {}
        libraries = body.get("libraries")
        db = Database()
        result = scan_poster_status(db, libraries=libraries)
        db.close()
        return jsonify(result)

    @app.route("/posters/apply-tmdb", methods=["POST"])
    def poster_apply_tmdb():
        """Apply TMDb poster. JSON: {"rating_key": N} or {"library": "..."}."""
        body = request.get_json(silent=True) or {}
        rating_key = body.get("rating_key")
        library = body.get("library")
        db = Database()
        if rating_key:
            ok, msg = apply_tmdb_poster(db, int(rating_key))
            db.close()
            return jsonify({"success": ok, "message": msg})
        elif library:
            result = apply_tmdb_bulk(db, library=library)
            db.close()
            return jsonify(result)
        else:
            db.close()
            return jsonify({"error": "rating_key or library required"}), 400

    @app.route("/posters/import-mediux", methods=["POST"])
    def poster_import_mediux():
        """Import Mediux YAML poster set. JSON: {"yaml_path": "..."}."""
        body = request.get_json(silent=True) or {}
        yaml_path = body.get("yaml_path")
        if not yaml_path:
            return jsonify({"error": "yaml_path required"}), 400
        db = Database()
        result = import_mediux_set(db, yaml_path)
        db.close()
        return jsonify(result)

    @app.route("/posters/enforce-locks", methods=["POST"])
    def poster_enforce_locks():
        """Re-lock any curated posters that Plex has unlocked."""
        db = Database()
        result = enforce_locks(db)
        db.close()
        return jsonify(result)

    @app.route("/posters/orphans")
    def poster_orphans():
        """Detect orphaned poster files not matching any Plex item."""
        result = detect_orphans()
        return jsonify(result)

    # ── Export endpoints ──────────────────────────────────────────────────

    @app.route("/export")
    def export_data():
        """Export search terms. Params: categories (comma-sep), library."""
        cats = request.args.get("categories")
        categories = [c.strip() for c in cats.split(",")] if cats else None
        library = request.args.get("library")
        db = Database()
        result = generate_export(db, categories=categories, library=library)
        db.close()
        return jsonify(result)

    @app.route("/export/download")
    def export_download():
        """Download export as plain text file. Params: categories (comma-sep), library."""
        cats = request.args.get("categories")
        categories = [c.strip() for c in cats.split(",")] if cats else None
        library = request.args.get("library")
        db = Database()
        text = generate_export_text(db, categories=categories, library=library)
        db.close()
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": "attachment; filename=plex-ops-export.txt"},
        )

    @app.route("/sonarr/series")
    def sonarr_series():
        analyzer = MediaAnalyzer()
        return jsonify(analyzer.get_sonarr_series())

    @app.route("/radarr/movies")
    def radarr_movies():
        analyzer = MediaAnalyzer()
        return jsonify(analyzer.get_radarr_movies())

    # ---- Wanted list ----

    @app.route("/wanted/search")
    def wanted_search():
        """Search Plex for a title. Anything with no match is captured to the wanted list."""
        query = request.args.get("q", "")
        if not query.strip():
            return jsonify({"error": "q required", "example": {"q": "Chef (2014)"}}), 400
        db = Database()
        try:
            result = search_and_capture(db, query)
        except PlexUnavailable as e:
            # An unreachable server must never be recorded as a missing film.
            return plex_trouble(e)
        finally:
            db.close()
        return jsonify(result)

    @app.route("/wanted")
    def wanted_list():
        """Wanted entries, newest first. Params: status (wanted|acquired|dismissed|all)."""
        status = request.args.get("status", "wanted")
        if status == "all":
            status = None
        elif status not in Database.WANTED_STATUSES:
            return jsonify({
                "error": f"unknown status {status!r}",
                "expected": list(Database.WANTED_STATUSES) + ["all"],
            }), 400
        db = Database()
        rows = db.get_wanted(status)
        counts = db.count_wanted()
        db.close()
        return jsonify({"items": [dict(r) for r in rows], "counts": counts})

    @app.route("/wanted/<int:wanted_id>/status", methods=["POST"])
    def wanted_set_status(wanted_id):
        """Set an entry's status: acquired, dismissed, or back to wanted."""
        body = request.get_json(silent=True) or {}
        status = body.get("status") or request.args.get("status")
        if not status:
            return jsonify({"error": "status required", "example": {"status": "acquired"}}), 400
        db = Database()
        try:
            row = db.set_wanted_status(wanted_id, status)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        finally:
            db.close()
        if row is None:
            return jsonify({"error": f"no wanted entry with id {wanted_id}"}), 404
        return jsonify(dict(row))

    @app.route("/wanted/export")
    def wanted_export():
        """Wanted titles as plain text, one `Title (Year)` per line."""
        db = Database()
        text = wanted_export_text(db)
        db.close()
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": "attachment; filename=wanted.txt"},
        )

    def settle_wanted(db):
        """Mark anything Plex has since acquired, and tell whoever asked.

        `recheck` only reports; this is the half that acts on it. Kept separate
        so a recheck can still be run without changing anything.
        """
        result = recheck_wanted(db)
        settled = []
        for entry in result.get("now_in_plex", []):
            row = db.set_wanted_status(entry["id"], "acquired")
            if row is None:
                continue
            name = entry["title"] + (f" ({entry['year']})" if entry.get("year") else "")
            settled.append(name)
            # Whoever asked hears about it; if nobody is recorded against the
            # row it was captured by a search, so the owner is told instead.
            # Whoever asked hears about it. If their id is unknown or stale -
            # older rows predate it being recorded - the owner is told rather
            # than the arrival passing in silence.
            owner = web_push.owner_id()
            for target in (entry.get("requested_by_id"), owner):
                if not target:
                    continue
                try:
                    sent = web_push.send_event(db, "arrived", target,
                                               url="/request",
                                               tag=f"arrived-{entry['id']}", film=name)
                except Exception:
                    sent = None
                if sent and sent.get("sent"):
                    break
        return {"checked": result.get("checked", 0), "settled": settled}

    @app.route("/wanted/settle", methods=["POST"])
    def wanted_settle():
        """Re-check the list, mark what has arrived, and notify the asker."""
        db = Database()
        try:
            return jsonify(settle_wanted(db))
        except PlexUnavailable as e:
            return plex_trouble(e)
        finally:
            db.close()

    @app.route("/plex/webhook", methods=["POST"])
    def plex_webhook():
        """Plex tells us it has added something; check the list against it.

        A Plex server sharing this machine posts from the loopback address, so
        the guard's local rule lets it through without a session. One running
        on another box does not, and its webhook is refused - the alternative
        is an endpoint anybody could post to on a published host, which is
        worse than waiting for the next recheck. Only `library.new` is acted
        on; the rest of Plex's events are noise here.
        """
        raw = request.form.get("payload") or request.data or b"{}"
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return jsonify({"ignored": "unreadable payload"}), 200
        event = payload.get("event")
        if event != "library.new":
            return jsonify({"ignored": event}), 200
        db = Database()
        try:
            outcome = settle_wanted(db)
        except PlexUnavailable as e:
            return plex_trouble(e)
        finally:
            db.close()
        return jsonify({"event": event, **outcome})

    @app.route("/wanted/recheck", methods=["GET", "POST"])
    def wanted_recheck():
        """Re-search every wanted entry and report the ones Plex now has."""
        db = Database()
        try:
            result = recheck_wanted(db)
        except PlexUnavailable as e:
            return plex_trouble(e)
        finally:
            db.close()
        return jsonify(result)

    # ---- Requests (the household-facing page) ----

    @app.route("/")
    @app.route("/request")
    def request_page():
        """The household page is what the shared link points at, so it lives at
        the root: the address people are given is just the hostname.

        The preview image has to be an absolute address or the phone ignores it
        and shows whatever icon it happened to cache before - which is why a
        shared link kept turning up in the old colours. The host is only known
        per request, so it is filled in here.
        """
        # A home-screen app arriving with a handoff code is a signed-in browser
        # install opening for the first time: trade the code for this app's own
        # session, so nobody signs in twice for one phone.
        code = request.args.get("handoff")
        if code:
            db = Database()
            try:
                session_token = plex_auth.redeem_handoff(db, code)
            finally:
                db.close()
            out = redirect("/")
            if session_token:
                out.set_cookie(
                    SESSION_COOKIE, session_token,
                    max_age=plex_auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", path="/",
                    secure=request.is_secure,
                )
            return out
        page = (DASHBOARD_DIR / "request.html").read_text(encoding="utf-8")
        origin = request.url_root.rstrip("/")
        return Response(page.replace("__ORIGIN__", origin), mimetype="text/html")

    @app.route("/manifest.webmanifest")
    def web_manifest():
        """Generated rather than static, so renaming the app is one config line.

        Fetched with credentials (the link tag says use-credentials): when the
        person adding to their home screen is signed in, start_url carries a
        one-time code so the installed app opens already signed in, instead of
        asking them to authenticate twice for one phone.
        """
        name = user_library.app_name()
        start_url = "/"
        user = current_user()
        if user:
            db = Database()
            try:
                start_url = "/?handoff=" + plex_auth.mint_handoff(db, user)
            finally:
                db.close()
        return jsonify({
            "name": name,
            "short_name": name,
            "description": "Ask for a film or a series and it turns up on Plex",
            "start_url": start_url,
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#14161a",
            "theme_color": "#14161a",
            "icons": [
                {"src": "/icons/icon-192.png?v=3", "sizes": "192x192",
                 "type": "image/png", "purpose": "any maskable"},
                {"src": "/icons/icon-512.png?v=3", "sizes": "512x512",
                 "type": "image/png", "purpose": "any maskable"},
            ],
        })

    @app.route("/persona")
    def persona_config():
        """What the assistant is called, and what it says for itself.

        Open, and asked for before anything else: the welcome screen draws the
        assistant's face and its greeting to somebody who has not signed in
        yet, so putting this behind the sign-in would mean a nameless page
        until the moment it no longer mattered.

        Only the four things the page draws with. The voice and the worked
        examples are instructions for the model and stay on this side of the
        wire, where nobody can read them back to it. No error path either -
        a persona block that is missing or nonsense comes back as the built-in
        defaults, because a page with no name on it is worse than a plain one.
        """
        p = persona.persona()
        out = jsonify({k: p[k] for k in ("name", "greeting", "brush_offs", "images")})
        # Not cached: an owner who renames the assistant and restarts should
        # see the new name on the next load, not whenever a browser feels like
        # asking again.
        out.headers["Cache-Control"] = "no-store"
        return out

    @app.route("/sw.js")
    def service_worker():
        """Served from the root so its scope covers the whole app."""
        response = send_from_directory(str(DASHBOARD_DIR), "sw.js", mimetype="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.route("/push/key")
    def push_key():
        return jsonify({"key": web_push.public_key()})

    @app.route("/push/subscribe", methods=["POST"])
    def push_subscribe():
        user = current_user()
        if not user:
            return jsonify({"error": "sign in first"}), 401
        body = request.get_json(silent=True) or {}
        db = Database()
        try:
            web_push.subscribe(db, user, body.get("subscription") or {})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        finally:
            db.close()
        return jsonify({"status": "ok"})

    @app.route("/push/unsubscribe", methods=["POST"])
    def push_unsubscribe():
        user = current_user()
        body = request.get_json(silent=True) or {}
        db = Database()
        try:
            # Only this person's own subscription, so one household member
            # cannot silence another's devices by naming their endpoint.
            web_push.unsubscribe(db, body.get("endpoint", ""),
                                 plex_user_id=user["id"])
        finally:
            db.close()
        return jsonify({"status": "ok"})

    @app.route("/notifications")
    def notifications_catalogue():
        """Everything that can be sent, and to whom. Owner only."""
        user = current_user()
        if not user or user.get("role") != "owner":
            return jsonify({"error": "owner only"}), 403
        db = Database()
        try:
            web_push.ensure_table(db)
            devices = db.conn.execute(
                "SELECT username, COUNT(*) n FROM push_subscriptions GROUP BY username"
            ).fetchall()
        finally:
            db.close()
        return jsonify({
            "notifications": web_push.describe(),
            "devices": [{"username": r["username"], "devices": r["n"]} for r in devices],
            "edit_in": "config.yaml, under notifications",
        })

    @app.route("/push/test", methods=["POST"])
    def push_test():
        """Send yourself one, to prove the round trip works."""
        user = current_user()
        if not user:
            return jsonify({"error": "sign in first"}), 401
        db = Database()
        try:
            result = web_push.send_event(db, "test", user["id"], url="/", tag="test")
        finally:
            db.close()
        return jsonify(result)

    @app.route("/icons/<path:filename>")
    def app_icon(filename):
        """Short cache: the artwork is still changing, and iOS holds on hard."""
        response = send_from_directory(str(DASHBOARD_DIR / "icons"), filename)
        response.headers["Cache-Control"] = "public, max-age=600"
        return response

    # ---- Sign in with Plex ----

    SESSION_COOKIE = "plexops_session"

    def current_user():
        """Whoever is signed in on this request, or None."""
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        db = Database()
        try:
            return plex_auth.session_user(db, token)
        finally:
            db.close()

    # Starting a sign-in is the only expensive thing a stranger can ask for:
    # each attempt makes two calls out to plex.tv under this server's own
    # client identity, and polling makes one per check. Published to the
    # internet that is an amplifier pointed at Plex with our name on it, so
    # attempts are rated, and a pin nobody here issued is refused outright
    # rather than passed along.
    # Behind NAT a whole household is one caller: everyone on the same wifi
    # shares a public address, so a per-caller count of three meant three
    # sign-ins for the entire house and the fourth person was refused. What
    # actually upset Plex was the RATE of a burst, not the total - so the
    # counts are generous and a short gap between attempts does the real work.
    PIN_WINDOW = 600
    PIN_PER_CALLER = 12
    PIN_OVERALL = 40
    PIN_GAP = 2.0        # seconds between attempts from one caller
    _pin_attempts = {}

    def caller():
        """Who is asking. Behind the proxy every request looks local, so the
        forwarded address is the only thing that tells callers apart - and it
        must be the entry the proxy itself added (the rightmost), not the
        leftmost, which the client writes and could rotate to dodge the limit."""
        forwarded = request.headers.get("X-Forwarded-For", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        return (parts[-1] if parts else None) or request.remote_addr or "unknown"

    def pin_allowed():
        """May this caller start a sign-in now?

        Returns (allowed, seconds_to_wait) so the page can say something
        better than "no".
        """
        now = time.time()
        for key, stamps in list(_pin_attempts.items()):
            fresh = [t for t in stamps if now - t < PIN_WINDOW]
            if fresh:
                _pin_attempts[key] = fresh
            else:
                del _pin_attempts[key]
        who = caller()
        mine = _pin_attempts.get(who, [])
        if mine and now - mine[-1] < PIN_GAP:
            return False, round(PIN_GAP - (now - mine[-1]) + 0.5)
        if len(mine) >= PIN_PER_CALLER:
            return False, round(PIN_WINDOW - (now - mine[0]))
        if sum(len(v) for v in _pin_attempts.values()) >= PIN_OVERALL:
            return False, 60
        _pin_attempts.setdefault(who, []).append(now)
        return True, 0

    # Each issued pin may be polled a bounded number of times before it is
    # retired, so a completed sign-in still has headroom but a flood cannot
    # run outbound calls to plex.tv without limit. The ceiling allows for two
    # tabs sharing one pin - the sign-in page reuses its pin across tabs.
    # A signed-in member can add to the wanted list freely, but not without
    # any ceiling - each insert can fire an owner push and runs Plex searches,
    # so a runaway client (or a bored teenager) is capped per hour. The number
    # sits with the other allowances, under app.limits, since it is the same
    # kind of decision about the same people.
    _wanted_adds = {}

    def wanted_add_allowed(uid):
        import time as _t
        now = _t.time()
        fresh = [t for t in _wanted_adds.get(str(uid), ()) if now - t < 3600]
        if len(fresh) >= ai_limits.limits()["wanted_per_hour"]:
            _wanted_adds[str(uid)] = fresh
            return False
        fresh.append(now)
        _wanted_adds[str(uid)] = fresh
        return True

    POLL_MAX = 900

    @app.route("/auth/start", methods=["POST"])
    def auth_start():
        """Begin a Plex login; the person types this code into plex.tv/link."""
        allowed, wait = pin_allowed()
        if not allowed:
            when = (f"{wait} seconds" if wait < 60
                    else f"{max(1, round(wait / 60))} minutes")
            return jsonify({
                "error": f"That is a lot of sign-ins at once - try again in {when}.",
                "retry_after": wait,
            }), 429
        try:
            pin = plex_auth.create_pin(request.url_root.rstrip("/"))
        except requests.HTTPError as e:
            # plex.tv itself refusing to mint pins is not an outage, it is a
            # cool-down - a burst of retries once bricked sign-in for the whole
            # household and the page reported it as Plex being unreachable.
            status = getattr(e.response, "status_code", None)
            if status == 429:
                return jsonify({
                    "error": "Plex is asking us to slow down - give it a minute "
                             "and try again.",
                    "retry_after": 60,
                }), 429
            return jsonify({"error": f"could not reach plex.tv: {e}"}), 502
        except Exception as e:
            return jsonify({"error": f"could not reach plex.tv: {e}"}), 502
        db = Database()
        try:
            plex_auth.remember_pins(db, pin.get("id"), pin.get("link_id"))
        finally:
            db.close()
        return jsonify(pin)

    @app.route("/auth/poll/<int:pin_id>")
    def auth_poll(pin_id):
        """Has the login finished? Sets a session cookie once it has.

        Two pins are outstanding - the typed code and the tapped link - so both
        are checked; the person only completes one.
        """
        also = request.args.get("also", type=int)
        # Only pins this server issued are ever asked about upstream, and each
        # may be polled a bounded number of times before it is retired. Without
        # this a single issued pin is an unlimited lever on plex.tv - the poll
        # path would otherwise sidestep the limit that /auth/start carries.
        db = Database()
        try:
            primary = pin_id if plex_auth.pin_known(db, pin_id) else None
            secondary = also if (also and plex_auth.pin_known(db, also)) else None
            if primary is None and secondary is None:
                # Say so, rather than "waiting": a pin this server has genuinely
                # forgotten - restarted past the purge window, or never issued -
                # will never complete, and a page told "waiting" waits for ever.
                return jsonify({
                    "status": "unknown",
                    "error": "That sign-in is no longer live - get a fresh code.",
                })
            if not plex_auth.count_poll(db, primary or secondary, POLL_MAX):
                return jsonify({"status": "waiting"})
        finally:
            db.close()
        try:
            # Only pins this server issued are ever asked about upstream - a
            # valid primary must not smuggle an unissued `also` through.
            token = plex_auth.check_pin(primary) if primary else None
            if not token and secondary:
                token = plex_auth.check_pin(secondary)
        except Exception as e:
            return jsonify({"error": str(e)}), 502
        if not token:
            return jsonify({"status": "waiting"})

        person = plex_auth.authorise(plex_auth.plex_identity(token))
        if not person:
            # A real Plex account, but not one this server is shared with.
            return jsonify({
                "status": "denied",
                "error": "That Plex account does not have access to this server.",
            }), 403

        # The pin flow yields their plex.tv ACCOUNT token; the server itself
        # only honours their per-server token, so exchange before storing.
        usable = plex_auth.server_access_token(token)
        if usable is None:
            # Shared on paper, but the invite was never accepted - their
            # account cannot reach this server at all, and a session would
            # just die at the first check. Say the one thing that fixes it.
            return jsonify({
                "status": "denied",
                "error": "Nearly there - Plex sent you an invite to this server "
                         "that hasn't been accepted yet. Find it in your email "
                         "or at plex.tv, accept it, then sign in here again.",
            }), 403

        db = Database()
        try:
            session_token = plex_auth.start_session(db, person, usable)
        finally:
            db.close()
        response = jsonify({"status": "ok", "user": person})
        response.set_cookie(
            SESSION_COOKIE, session_token, max_age=plex_auth.SESSION_DAYS * 86400,
            httponly=True, samesite="Lax", path="/",
            # Secure when the visit is - which is every visit that arrives
            # over https. Left conditional so the owner console on plain
            # http://127.0.0.1 keeps working.
            secure=request.is_secure,
        )
        return response

    def plex_trouble(e):
        """Plex's own error, made fit to show a person.

        plexapi puts the whole HTTP body in the message, so an unauthorised
        reply arrives as a page of HTML. Keep the first clause, drop any
        markup, and cap it.
        """
        text = re.sub(r"<[^>]*>", " ", str(e))
        text = " ".join(text.split())
        text = text.split(";")[0].split("http://")[0].strip()
        return jsonify({"error": f"Plex is unavailable: {text[:120] or 'no reply'}"}), 503

    def expired_session():
        """End a session whose Plex token has died and ask for a fresh sign-in.

        Answered as 401 with auth_required so the page's own handler takes
        over and shows the sign-in, rather than printing Plex's raw refusal.
        """
        db = Database()
        try:
            plex_auth.end_session(db, request.cookies.get(SESSION_COOKIE))
        finally:
            db.close()
        out = jsonify({
            "error": "Your Plex sign-in has expired. Sign in again to carry on.",
            "auth_required": True,
        })
        out.delete_cookie(SESSION_COOKIE, path="/")
        return out, 401

    @app.route("/auth/me")
    def auth_me():
        user = current_user()
        # A session can outlive the Plex token it holds - a password change or
        # a sign-out-everywhere kills every token at once. Catch it here, when
        # the page loads, rather than letting the app look signed in until a
        # search fails.
        if user and not plex_auth.token_alive(user.get("plex_token")):
            db = Database()
            try:
                plex_auth.end_session(db, request.cookies.get(SESSION_COOKIE))
            finally:
                db.close()
            gone = jsonify({"user": None, "app_name": user_library.app_name(),
                            "signed_out": "Your Plex sign-in expired - sign in again."})
            gone.delete_cookie(SESSION_COOKIE, path="/")
            return gone
        safe = None
        if user:
            safe = {k: v for k, v in user.items() if k != "plex_token"}
            safe["default_playlist"] = user_library.default_playlist_name(user["username"])
        return jsonify({"user": safe, "app_name": user_library.app_name()})

    @app.route("/auth/logout", methods=["POST"])
    def auth_logout():
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            db = Database()
            try:
                plex_auth.end_session(db, token)
            finally:
                db.close()
        response = jsonify({"status": "ok"})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # Nothing is reachable unless it is named here. The list is deliberately
    # short: the page shell and the sign-in itself are open, the household
    # surface needs a Plex account this server is shared with, and everything
    # else - scans, fixers, poster tools, exports, the dashboard - is the
    # owner's alone. Written this way round so that publishing the host more
    # widely cannot quietly expose an endpoint nobody remembered.
    OPEN_PATHS = {"/", "/request", "/health", "/manifest.webmanifest", "/persona", "/sw.js"}
    OPEN_PREFIXES = ("/auth/", "/icons/")
    HOUSEHOLD_PATHS = {"/wanted/thumb"}
    HOUSEHOLD_PREFIXES = ("/request/", "/push/")

    @app.before_request
    def guard():
        """Decide, for every request, which of the three tiers it falls in."""
        path = request.path
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return None

        user = current_user()
        if path in HOUSEHOLD_PATHS or path.startswith(HOUSEHOLD_PREFIXES):
            if user:
                return None
            return jsonify({"error": "sign in with Plex to use this",
                            "auth_required": True}), 401

        # Everything else is the operations surface. Reachable two ways: signed
        # in as the owner, or from a browser on the machine this is running on.
        # A proxy stamps X-Forwarded-For on everything it passes in, so a
        # request arriving without one cannot have come through it from
        # outside - which keeps the console to hand on the server itself
        # without opening it up once the address is published.
        #
        # That second door rests on the proxy in front always adding the
        # header. Every sane one does, and ProxyFix above trusts exactly one
        # hop for the same reason. Anyone putting something else in front, or
        # exposing this port directly to the internet, should say so here and
        # take the loopback rule out.
        if user and user.get("role") == "owner":
            return None
        if not request.headers.get("X-Forwarded-For") and \
                request.remote_addr in ("127.0.0.1", "::1"):
            return None
        # A person who opened one of these in a browser gets sent somewhere they
        # can actually sign in, rather than a page of JSON.
        # Only a browser gets sent to the sign-in; a script asking for */* gets
        # a straight refusal rather than a redirect it would have to follow.
        wants_page = "text/html" in (request.headers.get("Accept") or "")
        if request.method == "GET" and not user and wants_page:
            return redirect("/request")
        return jsonify({"error": "that part is the owner's"}), 403

    @app.route("/request/playlists")
    def request_playlists():
        """This person's own playlists, from their own Plex account."""
        user = current_user()
        try:
            lists = user_library.playlists_for(user.get("plex_token"))
        except Exception as e:
            return jsonify({"error": str(e), "playlists": []}), 200
        return jsonify({
            "playlists": lists,
            "default": user_library.default_playlist_name(user["username"]),
        })

    @app.route("/request/detail")
    def request_detail():
        """Everything about one film or series, for the long-press peek."""
        user = current_user()
        key = request.args.get("key", "")
        if not key.isdigit():
            return jsonify({"error": "which one?"}), 400
        try:
            detail = user_library.item_detail(user.get("plex_token"), key)
        except PermissionError:
            return jsonify({"error": "sign in again"}), 401
        except Exception as e:
            return jsonify({"error": str(e)[:120]}), 500
        if detail is None:
            return jsonify({"error": "not on the shelf"}), 404
        return jsonify(detail)

    @app.route("/request/playlist/items")
    def request_playlist_items():
        """What is in one of this person's playlists."""
        user = current_user()
        name = (request.args.get("name") or "").strip()
        if not name:
            return jsonify({"error": "which playlist?"}), 400
        try:
            films = user_library.playlist_items(user.get("plex_token"), name)
        except PermissionError:
            return jsonify({"error": "sign in again to see your playlists"}), 401
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        if films is None:
            return jsonify({"error": f"no playlist called {name!r}", "films": []}), 404
        return jsonify({"playlist": name, "films": films})

    @app.route("/request/playlist/remove", methods=["POST"])
    def request_playlist_remove():
        """Take films out of one of this person's own playlists."""
        user = current_user()
        body = request.get_json(silent=True) or {}
        name = " ".join((body.get("playlist") or "").split())[:80]
        keys = (body.get("rating_keys") or [])[:60]
        if not name or not keys:
            return jsonify({"error": "say which playlist and which films"}), 400
        try:
            result = user_library.remove_from_playlist(user.get("plex_token"), name, keys)
        except PermissionError:
            return jsonify({"error": "sign in again to change your playlists"}), 401
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify(result)

    def add_season_detail(result):
        """Say which seasons of a series exist that are not held.

        A library looks complete right up until somebody checks: holding series
        one of something with four is worth knowing, and is the difference
        between "we have that" and "we have some of that".
        """
        for show in result.get("shows") or []:
            try:
                exists = tmdb_lookup.seasons_that_exist(show["title"], show.get("year"))
                held = set(show.get("held_seasons") or [])
                show["all_seasons"] = [s["season"] for s in exists]
                show["missing_seasons"] = [s["season"] for s in exists if s["season"] not in held]
            except Exception:
                show["all_seasons"], show["missing_seasons"] = [], []
        return result

    @app.route("/request/search", methods=["POST"])
    def request_search():
        """Plain-English film request -> films from this library."""
        body = request.get_json(silent=True) or {}
        query = ai_limits.clean_query(body.get("query"))
        user = current_user()
        who = user["username"] if user else (body.get("who") or "").strip()[:40]
        if not query:
            return jsonify({"error": "tell me what you fancy watching"}), 400

        # Naming a film - or a director, or an actor - is a straight library
        # lookup and costs nothing, so both are answered before anyone's
        # allowance is considered.
        try:
            by_title = (search_by_title(query, user.get("plex_token"))
                        or search_by_person(query, user.get("plex_token")))
        except PlexTokenDead:
            # Their Plex sign-in died under them - ask for a new one rather
            # than reporting an outage they cannot do anything about.
            return expired_session()
        except PlexUnavailable as e:
            return plex_trouble(e)
        if by_title:
            by_title["smart_mode"] = has_api_key()
            by_title["captured_missing"] = []
            return jsonify(add_season_detail(by_title))

        # Reserved before the call rather than recorded after it: a burst of
        # requests arriving together would otherwise all pass a check none of
        # them had yet paid for.
        db = Database()
        try:
            allowed, message, _left = ai_limits.check(db, user)
            if allowed:
                reservation = ai_limits.record(db, user, "search")
        finally:
            db.close()
        if not allowed:
            return jsonify({
                "error": message, "rate_limited": True,
                "hint": "You can still search for a film by name, and ask for anything we don't have.",
            }), 429

        def give_back():
            # A search that errors before it reaches the model must not spend
            # the reservation it took; hand it back on every failure path.
            if reservation:
                gb = Database()
                try:
                    ai_limits.refund(gb, reservation)
                finally:
                    gb.close()

        try:
            result = mood_search(query, token=user.get("plex_token"))
        except PlexTokenDead:
            give_back()
            return expired_session()
        except PlexUnavailable as e:
            give_back()
            return plex_trouble(e)
        except Exception as e:
            give_back()
            return jsonify({"error": str(e)}), 500

        # Films Plex lists but can no longer play are no use for a playlist.
        # Put them on the wanted list instead - a search that turns up a hole in
        # the library should record it, not silently skip it.
        captured = []
        if result.get("unavailable"):
            db = Database()
            try:
                for film in result["unavailable"]:
                    note = "File missing from disk; surfaced by a search"
                    if who:
                        note += f" ({who})"
                    row, created = db.insert_wanted(
                        film["title"], film["year"], query, notes=note
                    )
                    if created:
                        captured.append({"title": film["title"], "year": film["year"]})
                # A file quietly disappearing is worth knowing about, so the
                # owner hears once per search that turns one up.
                if captured:
                    try:
                        owner = next(
                            (uid for uid, info in plex_auth.allowed_users().items()
                             if info["role"] == "owner"), None
                        )
                        if owner:
                            names = ", ".join(
                                f["title"] + (f" ({f['year']})" if f["year"] else "")
                                for f in captured[:3]
                            )
                            # Tagged per batch, not with one shared name - a
                            # shared tag means a second search's alert replaces
                            # the first on the owner's screen before it is read.
                            web_push.send_event(
                                db, "file_missing", owner, url="/dashboard",
                                tag=f"missing-{captured[0]['title'][:40]}", film=names,
                            )
                    except Exception:
                        pass
            finally:
                db.close()
        # Nothing in the library matches a title they named: work out which film
        # they mean, so what goes on the list is a real one with a year on it.
        if result.get("kind") == "title_missing":
            # Parse the query rather than trusting the mood result to carry a
            # title: it does not, so this was searching TMDb for "Taxi (French)"
            # rather than for Taxi.
            named, said_year = parse_query(query)
            result["did_you_mean"] = tmdb_lookup.suggest(
                named or query, hint=query_hint(query), year=said_year
            )
            # A film still on at the pictures cannot be put on anybody's shelf,
            # so say so rather than offering to add it and quietly never
            # delivering. Only the top few are checked - one call each, and
            # nobody reads past them.
            for guess in result["did_you_mean"][:3]:
                try:
                    state, when = tmdb_lookup.availability(guess.get("tmdb_id"))
                except Exception:
                    state, when = "out", None
                guess["release_state"] = state
                guess["available_from"] = when
            # It might be a series rather than a film, in which case the useful
            # answer is which series, and whether they want all of it.
            shows = tmdb_lookup.suggest_shows(named or query, hint=query_hint(query))
            for show in shows[:3]:
                try:
                    show["all_seasons"] = [
                        s["season"] for s in tmdb_lookup.seasons_that_exist(show["title"], show.get("year"))
                    ]
                except Exception:
                    show["all_seasons"] = []
            result["did_you_mean_shows"] = shows
            # Anything in the library with a similar name, offered as a maybe
            # rather than presented as the answer.
            try:
                result["close"] = close_matches(query, user.get("plex_token"))
            except PlexUnavailable:
                result["close"] = []

        add_season_detail(result)
        result["captured_missing"] = captured
        result["smart_mode"] = has_api_key()
        # Answered from cache or by keywords: the model was never asked, so give
        # the reservation back.
        if result.get("translated_by") != "model" and reservation:
            db = Database()
            try:
                ai_limits.refund(db, reservation)
            finally:
                db.close()
        return jsonify(result)

    @app.route("/request/playlist", methods=["POST"])
    def request_playlist():
        """Add films to one of this person's playlists, creating it if needed.

        The playlist is made with their own Plex token, so it belongs to them
        and shows up in their Plex app rather than in the owner's.
        """
        user = current_user()
        body = request.get_json(silent=True) or {}
        name = " ".join((body.get("playlist") or "").split())[:80]
        keys = (body.get("rating_keys") or [])[:60]
        if not keys:
            return jsonify({"error": "pick at least one film"}), 400
        try:
            result = user_library.add_to_playlist(
                user.get("plex_token"), name, keys, user["username"]
            )
        except PermissionError:
            return jsonify({"error": "sign in again to use your own playlists"}), 401
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify(result)

    @app.route("/request/playlist/auto", methods=["POST"])
    def request_playlist_auto():
        """Turn a description straight into a playlist of films we already have.

        Only the library is drawn on - this never proposes something that would
        have to be fetched.
        """
        user = current_user()
        body = request.get_json(silent=True) or {}
        query = ai_limits.clean_query(body.get("query"))
        count = min(int(body.get("count") or 12), 40)
        if not query:
            return jsonify({"error": "say what the playlist should be"}), 400

        # Everything free happens before the meter starts: junk and chancers
        # used to be charged for, because the allowance was taken first and
        # only then was the request looked at.
        if not worth_asking(query):
            return jsonify({"error": "Say what the playlist should be - a mood, a genre, an occasion."}), 400
        if off_topic(query):
            return jsonify({"kind": "cheeky", "interpretation": persona.brush_off(),
                            "matches": [], "added": 0}), 200

        db = Database()
        try:
            allowed, message, _left = ai_limits.check(db, user)
            reservation = ai_limits.record(db, user, "playlist") if allowed else None
        finally:
            db.close()
        if not allowed:
            return jsonify({"error": message, "rate_limited": True}), 429

        def give_back():
            # A build that hands the person nothing must not cost them a turn.
            # The search endpoint refunds on every failure path; this one used
            # to keep the charge, so an outage or an empty answer quietly ate
            # the day's allowance.
            if reservation:
                gb = Database()
                try:
                    ai_limits.refund(gb, reservation)
                finally:
                    gb.close()

        try:
            found = mood_search(query, limit=count, token=user.get("plex_token"))
        except PlexUnavailable as e:
            give_back()
            return plex_trouble(e)
        if not found["matches"]:
            give_back()
            return jsonify({"error": "nothing in the library matches that",
                            "interpretation": found["interpretation"]}), 404

        name = (body.get("playlist") or found["playlist_name"]).strip()
        try:
            result = user_library.add_to_playlist(
                user.get("plex_token"), name,
                [m["rating_key"] for m in found["matches"]], user["username"],
            )
        except PermissionError:
            give_back()
            return jsonify({"error": "sign in again to use your own playlists"}), 401
        except Exception as e:
            give_back()
            return jsonify({"error": str(e)}), 500
        result["interpretation"] = found["interpretation"]
        result["films"] = [{"title": m["title"], "year": m["year"]} for m in found["matches"]]
        # Answered from cache or by keywords: the model was never asked, so the
        # reservation goes back - the same rule the search applies.
        if found.get("translated_by") != "model":
            give_back()

        # Optionally round the list out with films that fit but are not here.
        # They cannot go in a Plex playlist - there is nothing to point at - so
        # they go on the wanted list instead, and the playlist grows into them.
        result["also_wanted"] = []
        if body.get("include_missing"):
            have = [m["title"] for m in found["matches"]]
            candidates = tmdb_lookup.discover(found.get("filters") or {}, exclude_titles=have, limit=8)
            db = Database()
            try:
                for film in candidates:
                    # Cheap, and it stops us asking for something on the shelf:
                    # discovery only knows what we showed it, not the library.
                    try:
                        if search_by_title(f"{film['title']} ({film['year']})" if film["year"]
                                           else film["title"], user.get("plex_token")):
                            continue
                    except PlexUnavailable:
                        break
                    row, created = db.insert_wanted(
                        film["title"], film["year"], query,
                        notes=f"Suggested while building “{name}”"
                             + (f" for {user['username']}" if user else ""),
                    )
                    if created:
                        db.conn.execute(
                            "UPDATE wanted SET requested_by = ?, requested_by_id = ? WHERE id = ?",
                            (user["username"], user["id"], row["id"]),
                        )
                        db.conn.commit()
                        result["also_wanted"].append({
                            "title": film["title"], "year": film["year"],
                            "poster": film["poster"], "rating": film["rating"],
                        })
                    if len(result["also_wanted"]) >= 5:
                        break
            finally:
                db.close()
        return jsonify(result)

    @app.route("/request/showcase")
    def request_showcase():
        """Poster rows for the front page - no model involved, so no allowance."""
        user = current_user()
        try:
            return jsonify({"rows": showcase(token=user.get("plex_token"))})
        except PlexUnavailable as e:
            return jsonify({"rows": [], "error": str(e)}), 200
        except Exception as e:
            return jsonify({"rows": [], "error": str(e)}), 200

    @app.route("/request/suggestions")
    def request_suggestions():
        """A few films to open someone's list with, from what they have watched."""
        user = current_user()
        try:
            picks, reason = suggest.starter_picks(user["id"], limit=3, token=user.get("plex_token"))
        except Exception as e:
            return jsonify({"error": str(e), "picks": []}), 200
        return jsonify({"picks": picks, "reason": reason,
                        "playlist": user_library.default_playlist_name(user["username"])})

    @app.route("/request/announcements")
    def request_announcements():
        """Notes the owner has put up for the household."""
        db = Database()
        try:
            notes = [dict(r) for r in db.announcements()]
        finally:
            db.close()
        return jsonify({"notes": notes})

    @app.route("/announce", methods=["POST"])
    def announce():
        """Put a note up, and tell everyone's phone about it.

        Owner tier - the guard already refuses anyone else, since this is not
        under /request. Deliberately a thing the owner does on purpose: a
        household does not want the app deciding when to buzz everybody's
        phone.
        """
        user = current_user()
        body = " ".join((request.get_json(silent=True) or {}).get("body", "").split())[:280]
        if not body:
            return jsonify({"error": "say something first"}), 400
        db = Database()
        try:
            note = db.post_announcement(body, (user or {}).get("username", "the owner"))
            # Titled with whatever this install calls itself, so the phone
            # shows the name people know it by rather than a name baked in
            # here that stopped being true the moment somebody renamed it.
            told = web_push.broadcast(
                db, user_library.app_name(), body, url="/request",
                tag=f"note-{note['id']}", exclude_user=(user or {}).get("id"),
            )
        finally:
            db.close()
        return jsonify({"note": dict(note), "sent": told.get("sent", 0)})

    @app.route("/announce/<int:note_id>/retire", methods=["POST"])
    def announce_retire(note_id):
        """Take a note down for everyone."""
        db = Database()
        try:
            db.retire_announcement(note_id)
        finally:
            db.close()
        return jsonify({"status": "ok"})

    @app.route("/request/nudges")
    def request_nudges():
        """Films they have not seen that follow from ones they have.

        Costs a model call, so it is charged against the same allowance as a
        search and answered from cache-friendly data - it is asked for once
        when the page settles, not on every keystroke.
        """
        user = current_user()
        age = max(request.args.get("age", type=int) or 0, 0)
        db = Database()
        try:
            allowed, message, _left = ai_limits.check(db, user)
            reservation = ai_limits.record(db, user, "nudge") if allowed else None
        finally:
            db.close()
        if not allowed:
            return jsonify({"picks": [], "rate_limited": True, "error": message}), 200

        def give_back():
            if reservation:
                gb = Database()
                try:
                    ai_limits.refund(gb, reservation)
                finally:
                    gb.close()

        try:
            picks = suggest.taste_nudges(user["id"], limit=3,
                                         token=user.get("plex_token"), age=age)
        except Exception as e:
            give_back()
            return jsonify({"picks": [], "error": str(e)[:120]}), 200
        # Nothing came back - usually no watch history or a swallowed model
        # error, neither of which reached the model's answer. Someone whose
        # nudges never work must not spend their allowance finding that out.
        if not picks:
            give_back()
        return jsonify({"picks": picks})

    @app.route("/request/wanted", methods=["POST"])
    def request_wanted():
        """Ask for a film the library does not have - it lands on the wanted list."""
        body = request.get_json(silent=True) or {}
        title = " ".join((body.get("title") or "").split())[:200]
        kind = "show" if body.get("kind") == "show" else "film"
        season = body.get("season")
        season = int(season) if str(season).isdigit() and 0 < int(season) < 100 else None
        user = current_user()
        who = user["username"] if user else (body.get("who") or "").strip()[:40]
        if not title:
            return jsonify({"error": "which film?"}), 400
        # The cap belongs here, on the asking, not on the searching. It sat on
        # /request/search for a while, where it locked out anyone who merely
        # browsed forty times in an hour - while this endpoint, the one that
        # actually inserts a row, runs a Plex search and pages the owner per
        # title, had no ceiling at all.
        if user and not wanted_add_allowed(user["id"]):
            return jsonify({
                "error": "That's a lot of requests in one go - give it an hour.",
                "rate_limited": True,
            }), 429
        db = Database()
        try:
            if kind == "show":
                # A series is not looked up as a film, and a season request is
                # about one season, so it is recorded directly.
                named, said_year = parse_query(title)
                row, created = db.insert_wanted(
                    named or title, said_year, title, kind="show", season=season
                )
                result = {
                    "found": False, "kind": "show", "season": season,
                    "title": named or title, "year": said_year,
                    "added_to_wanted": created, "already_wanted": not created,
                    "wanted": dict(row), "matches": [],
                }
            else:
                result = search_and_capture(db, title)
            if result.get("added_to_wanted") and who:
                db.conn.execute(
                    "UPDATE wanted SET notes = ?, requested_by = ?, requested_by_id = ? WHERE id = ?",
                    (f"Requested by {who}", who, (user or {}).get("id"), result["wanted"]["id"]),
                )
                db.conn.commit()
                result["wanted"]["notes"] = f"Requested by {who}"
                # Tell the owner someone is waiting on something.
                try:
                    owner = next(
                        (uid for uid, info in plex_auth.allowed_users().items()
                         if info["role"] == "owner"), None
                    )
                    if owner and str(owner) != str((user or {}).get("id")):
                        label = result["title"] + (f" ({result['year']})" if result.get("year") else "")
                        # Tagged per entry: a shared tag makes each notification
                        # replace the one before it, so two people asking for
                        # two films inside a few minutes left the owner seeing
                        # only whichever came second.
                        web_push.send_event(
                            db, "requested", owner, url="/dashboard",
                            tag=f"request-{result['wanted']['id']}",
                            who=who, film=label,
                        )
                except Exception:
                    pass
        except PlexUnavailable as e:
            return plex_trouble(e)
        finally:
            db.close()
        return jsonify(result)

    @app.route("/request/wanted/list")
    def request_wanted_list():
        """What the household has asked for, newest first.

        Behind the sign-in guard, unlike the operations endpoint of the same
        name, so a shared user sees it only once they have signed in.
        """
        db = Database()
        try:
            rows = [dict(r) for r in db.get_wanted(None)]
            counts = db.count_wanted()
        finally:
            db.close()
        items = [{
            "id": r["id"],
            "title": r["title"],
            "year": r["year"],
            "kind": r.get("kind") or "film",
            "season": r.get("season"),
            "status": r["status"],
            "requested_by": r.get("requested_by"),
            "added_at": r["added_at"],
        } for r in rows]
        return jsonify({"items": items, "counts": counts})

    @app.route("/wanted/thumb")
    def wanted_thumb():
        """Proxy Plex artwork so the browser never needs the Plex token."""
        path = request.args.get("path", "")
        # Artwork only: /library/metadata/<n>/thumb|art|poster/... . Anything
        # else under /library/ is not this route's business and must not be
        # proxied with the owner's admin token.
        if ".." in path or not re.match(
                r"^/library/metadata/\d+/(thumb|art|poster|composite)/", path):
            return jsonify({"error": "not a Plex artwork path"}), 400
        # Ask Plex's photo transcoder for a thumbnail rather than pulling the
        # full-size poster (often 2-3 MB) for a 74px slot.
        width = min(request.args.get("width", 200, type=int), 1200)
        height = min(request.args.get("height", 300, type=int), 800)
        try:
            r = requests.get(
                config["plex"]["url"] + "/photo/:/transcode",
                params={
                    "width": width,
                    "height": height,
                    "minSize": 1,
                    "upscale": 0,
                    "url": path,
                },
                headers={"X-Plex-Token": config["plex"]["token"]},
                timeout=15,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            return jsonify({"error": f"Plex is unavailable: {e}"}), 502
        return Response(
            r.content,
            mimetype=r.headers.get("content-type", "image/jpeg"),
            headers={"Cache-Control": "public, max-age=86400"},
        )

    return app
