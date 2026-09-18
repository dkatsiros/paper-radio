"""
server.py — Private podcast RSS feed server (read-only).

Podcast apps (Apple Podcasts, Pocket Casts, Overcast, ...) cannot log in, so
the feed and its audio enclosures must be fetchable with NO auth. Privacy is
achieved through an unguessable 32-char token embedded in the URL path:

    GET|HEAD /podcast/<token>/feed.xml     → the RSS 2.0 feed (application/rss+xml)
    GET|HEAD /podcast/<token>/ep/<id>.mp3  → an episode enclosure (audio/mpeg, Range)
    GET|HEAD /podcast/<token>/cover.jpg    → the show cover art (image/jpeg)

HEAD matters: Apple Podcasts' "Follow a Show by URL" sends a HEAD preflight to
feed.xml before it will GET it. If HEAD 405s, Apple silently refuses to add the
show. Every route below answers HEAD, and tests/test_feed.py locks that in.

Content is public research papers rendered to audio — no sensitive data — so
obscurity of the token is a sufficient privacy boundary.

Run standalone:
    uvicorn paper_radio.server:app --host 0.0.0.0 --port 8000
or mount `router` into an existing FastAPI app.
"""
import hmac
import os
import re

from fastapi import APIRouter, FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from . import config

router = APIRouter()

# Keep the token URL out of search indexes without blocking podcast fetchers.
_NOINDEX = {"X-Robots-Tag": "noindex, nofollow"}


def _paths():
    store = config.store_dir()
    return {
        "audio": os.path.join(store, "audio"),
        "token": os.path.join(store, "token.txt"),
        "feed": os.path.join(store, "feed.xml"),
        "cover": os.path.join(store, "cover.jpg"),
    }


def _stored_token() -> str:
    """Read the current podcast token. Empty string if not initialised yet."""
    try:
        with open(_paths()["token"], "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _check_token(token: str) -> None:
    """Constant-time token check. 404 (not 403) on mismatch so the path leaks nothing."""
    stored = _stored_token()
    if not stored or not hmac.compare_digest(token, stored):
        raise HTTPException(status_code=404, detail="Not found")


def _serve_file_with_range(request: Request, path: str, media_type: str):
    """Serve a file honouring HTTP Range requests (needed for seek/scrub)."""
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Not found")
    file_size = os.path.getsize(path)
    range_header = request.headers.get("Range")

    if range_header:
        range_val = range_header.replace("bytes=", "")
        start_str, _, end_str = range_val.partition("-")
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        end = min(end, file_size - 1)
        if start > end or start >= file_size:
            raise HTTPException(status_code=416, detail="Range Not Satisfiable")
        chunk_size = end - start + 1

        def range_iter():
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = fh.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
            **_NOINDEX,
        }
        return StreamingResponse(range_iter(), status_code=206, media_type=media_type, headers=headers)

    def full_iter():
        with open(path, "rb") as fh:
            while chunk := fh.read(65536):
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size), **_NOINDEX}
    return StreamingResponse(full_iter(), media_type=media_type, headers=headers)


@router.api_route("/podcast/{token}/feed.xml", methods=["GET", "HEAD"])
async def podcast_feed(token: str, request: Request):
    """Serve the RSS 2.0 feed. No auth — token in the path is the credential."""
    _check_token(token)
    feed = _paths()["feed"]
    if not os.path.isfile(feed):
        raise HTTPException(status_code=404, detail="Feed not generated yet")
    return FileResponse(
        feed,
        media_type="application/rss+xml; charset=utf-8",
        headers=dict(_NOINDEX),
    )


@router.api_route("/podcast/{token}/cover.jpg", methods=["GET", "HEAD"])
async def podcast_cover(token: str, request: Request):
    """Serve the show cover art."""
    _check_token(token)
    cover = _paths()["cover"]
    if not os.path.isfile(cover):
        raise HTTPException(status_code=404, detail="Cover not found")
    return FileResponse(cover, media_type="image/jpeg", headers=dict(_NOINDEX))


@router.api_route("/podcast/{token}/ep/{ep_id}.mp3", methods=["GET", "HEAD"])
async def podcast_episode(token: str, ep_id: str, request: Request):
    """Serve an episode mp3 enclosure with Range support."""
    _check_token(token)
    if not re.match(r"^[A-Za-z0-9._-]+$", ep_id):
        raise HTTPException(status_code=404, detail="Not found")
    audio_dir = _paths()["audio"]
    path = os.path.realpath(os.path.join(audio_dir, ep_id + ".mp3"))
    root = os.path.realpath(audio_dir)
    if not path.startswith(root + os.sep):
        raise HTTPException(status_code=404, detail="Not found")
    return _serve_file_with_range(request, path, "audio/mpeg")


# Standalone app for `uvicorn paper_radio.server:app`.
app = FastAPI(title="Paper Radio feed")
app.include_router(router)
