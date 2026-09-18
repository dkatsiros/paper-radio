"""
config.py — Central configuration for Paper Radio.

Everything a self-hoster needs to change lives in the environment (see
.env.example). Nothing here is user-specific; all defaults are generic and
safe to publish. Values are read lazily so tests can override os.environ.

Load order:
  1. Real process environment.
  2. A .env file in the repo root, if python-dotenv is installed (optional).
"""
import os

try:  # optional convenience — the pipeline works without it
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default):
    val = os.environ.get(name)
    if not val:
        return list(default)
    return [c.strip() for c in val.split(",") if c.strip()]


# --- Public serving -------------------------------------------------------
# The externally reachable base URL where server.py is exposed (https).
# Used to build absolute feed + enclosure URLs the podcast apps will fetch.
def public_base() -> str:
    return os.environ.get("PAPER_RADIO_PUBLIC_BASE", "http://localhost:8000").rstrip("/")


# Where the feed store lives (token.txt, episodes.json, feed.xml, audio/, cover.jpg).
def store_dir() -> str:
    return os.environ.get(
        "PAPER_RADIO_STORE",
        os.path.join(os.getcwd(), "data", "podcast"),
    )


# --- Show (channel) metadata ---------------------------------------------
def show_title() -> str:
    return os.environ.get("PAPER_RADIO_SHOW_TITLE", "Paper Radio")


def show_description() -> str:
    return os.environ.get(
        "PAPER_RADIO_SHOW_DESCRIPTION",
        "Research papers turned into short audio deep-dives. Each episode is a "
        "NotebookLM audio overview of a paper worth reading. Machine-generated "
        "narration, human-picked papers.",
    )


def show_author() -> str:
    return os.environ.get("PAPER_RADIO_SHOW_AUTHOR", "Paper Radio")


def owner_name() -> str:
    return os.environ.get("PAPER_RADIO_OWNER_NAME", show_author())


def owner_email() -> str:
    return os.environ.get("PAPER_RADIO_OWNER_EMAIL", "owner@example.com")


def language() -> str:
    return os.environ.get("PAPER_RADIO_LANGUAGE", "en")


def categories():
    # Apple category taxonomy: https://podcasters.apple.com/support/1691
    return _list("PAPER_RADIO_CATEGORIES", ["Technology", "Science"])


def explicit() -> bool:
    return _bool("PAPER_RADIO_EXPLICIT", False)


def copyright_line() -> str:
    return os.environ.get("PAPER_RADIO_COPYRIGHT", f"© {show_author()}")


def timezone() -> str:
    return os.environ.get("PAPER_RADIO_TZ", "UTC")


# --- Generation (NotebookLM) ---------------------------------------------
def notebooklm_bin() -> str:
    return os.environ.get("PAPER_RADIO_NOTEBOOKLM_BIN", "notebooklm")


def ffmpeg_bin() -> str:
    return os.environ.get("PAPER_RADIO_FFMPEG_BIN", "ffmpeg")


def audio_format() -> str:
    # NotebookLM Audio Overview style: deep-dive | brief | debate
    return os.environ.get("PAPER_RADIO_AUDIO_FORMAT", "deep-dive")


def mp3_bitrate() -> str:
    return os.environ.get("PAPER_RADIO_MP3_BITRATE", "128k")
