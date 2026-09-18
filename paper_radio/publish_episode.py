#!/usr/bin/env python3
"""
publish_episode.py — Add an episode to your private podcast feed.

    python -m paper_radio.publish_episode \
        --mp3 /path/to/episode.mp3 \
        --title "Attention Is All You Need" \
        --description "A deep dive into the Transformer paper..." \
        --source-url "https://arxiv.org/abs/1706.03762"

What it does (all atomic + idempotent):
  1. Ensures the podcast store + unguessable token exist (creates on first run).
  2. Copies the mp3 into the served dir as <id>.mp3  (id = sha1(source-url)[:12]).
  3. Appends/updates the episode in <store>/episodes.json
     (dedupe by source-url — re-running with the same source-url UPDATES in place).
  4. Regenerates <store>/feed.xml atomically (temp file + os.replace).
  5. Prints the feed URL + the episode's public enclosure URL.

Idempotent: same --source-url ⇒ same id ⇒ same file ⇒ the item is replaced,
never duplicated. Safe to re-run.

Show (channel) metadata + base URL + store come from paper_radio.config
(environment / .env). Duration is read from the mp3 automatically (mutagen if
importable, else a pure-python parser). Override with --duration SECONDS.
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from . import config


# ---------------------------------------------------------------------------
# MP3 duration (pure python; handles CBR + VBR/Xing/Info). Returns seconds (int).
# ---------------------------------------------------------------------------
_BITRATES = {
    ("1", "3"): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0],
    ("2", "3"): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
    ("1", "1"): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, 0],
    ("1", "2"): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, 0],
    ("2", "1"): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, 0],
    ("2", "2"): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0],
}
_SAMPLE_RATES = {
    "1": [44100, 48000, 32000, 0],
    "2": [22050, 24000, 16000, 0],
    "2.5": [11025, 12000, 8000, 0],
}


def _mp3_duration_seconds(path):
    """Best-effort mp3 duration in seconds. Tries mutagen, then a pure parser."""
    try:
        from mutagen.mp3 import MP3  # type: ignore
        return int(round(MP3(path).info.length))
    except Exception:
        pass
    try:
        return _mp3_duration_pure(path)
    except Exception as exc:
        raise RuntimeError(f"could not read mp3 duration: {exc}") from exc


def _mp3_duration_pure(path):
    with open(path, "rb") as fh:
        data = fh.read()
    file_size = len(data)
    pos = 0
    if data[:3] == b"ID3":
        size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
               ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        pos = 10 + size
    n = len(data)
    while pos < n - 4:
        if data[pos] == 0xFF and (data[pos + 1] & 0xE0) == 0xE0:
            break
        pos += 1
    else:
        raise ValueError("no mp3 frame sync found")

    h = data[pos:pos + 4]
    ver_bits = (h[1] >> 3) & 0x03
    version = {0: "2.5", 2: "2", 3: "1"}.get(ver_bits)
    layer_bits = (h[1] >> 1) & 0x03
    layer = {1: "3", 2: "2", 3: "1"}.get(layer_bits)
    if version is None or layer is None:
        raise ValueError("unsupported mpeg version/layer")
    br_key = ("1" if version == "1" else "2", layer)
    bitrate_idx = (h[2] >> 4) & 0x0F
    sr_idx = (h[2] >> 2) & 0x03
    bitrate = _BITRATES[br_key][bitrate_idx]
    sample_rate = _SAMPLE_RATES[version][sr_idx]
    if bitrate == 0 or sample_rate == 0:
        raise ValueError("free/reserved bitrate or sample rate")
    samples_per_frame = 1152 if (version == "1" and layer == "3") else \
        (576 if layer == "3" else 384 if layer == "1" else 1152)

    xing_off = pos + 4 + (32 if version == "1" else 17)
    tag = data[xing_off:xing_off + 4]
    if tag in (b"Xing", b"Info"):
        flags = int.from_bytes(data[xing_off + 4:xing_off + 8], "big")
        if flags & 0x1:
            frames = int.from_bytes(data[xing_off + 8:xing_off + 12], "big")
            return int(round(frames * samples_per_frame / sample_rate))
    if data[pos + 36:pos + 40] == b"VBRI":
        frames = int.from_bytes(data[pos + 50:pos + 54], "big")
        return int(round(frames * samples_per_frame / sample_rate))

    audio_bytes = file_size - pos
    return int(round(audio_bytes * 8 / (bitrate * 1000)))


def _fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------
def store_paths(store):
    return {
        "store": store,
        "audio": os.path.join(store, "audio"),
        "token": os.path.join(store, "token.txt"),
        "episodes": os.path.join(store, "episodes.json"),
        "feed": os.path.join(store, "feed.xml"),
        "cover": os.path.join(store, "cover.jpg"),
    }


def ensure_store(store):
    p = store_paths(store)
    os.makedirs(p["audio"], exist_ok=True)
    if not os.path.exists(p["token"]):
        token = secrets.token_hex(16)  # 32 hex chars
        _atomic_write(p["token"], token)
        print(f"[init] generated podcast token: {token}", file=sys.stderr)
    return p


def get_token(store):
    p = store_paths(store)
    with open(p["token"], "r", encoding="utf-8") as fh:
        return fh.read().strip()


def load_episodes(store):
    p = store_paths(store)
    if not os.path.exists(p["episodes"]):
        return []
    with open(p["episodes"], "r", encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write(path, text, mode="w"):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".swap")
    try:
        with os.fdopen(fd, mode, encoding=None if "b" in mode else "utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def feed_url(store):
    return f"{config.public_base()}/podcast/{get_token(store)}/feed.xml"


# ---------------------------------------------------------------------------
# Feed generation
# ---------------------------------------------------------------------------
def build_feed_xml(store, episodes):
    token = get_token(store)
    base = f"{config.public_base()}/podcast/{token}"
    self_url = f"{base}/feed.xml"
    cover_url = f"{base}/cover.jpg"
    now_rfc = format_datetime(datetime.now(timezone.utc))

    eps_sorted = sorted(episodes, key=lambda e: e["pubdate_ts"], reverse=True)

    items = []
    for ep in eps_sorted:
        enclosure_url = f"{base}/ep/{ep['id']}.mp3"
        pub = format_datetime(datetime.fromtimestamp(ep["pubdate_ts"], timezone.utc))
        desc = ep.get("description", "")
        items.append(f"""    <item>
      <title>{escape(ep['title'])}</title>
      <description><![CDATA[{desc}]]></description>
      <itunes:summary><![CDATA[{desc}]]></itunes:summary>
      <link>{escape(ep.get('source_url', self_url))}</link>
      <enclosure url="{escape(enclosure_url)}" length="{ep['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="false">{escape(ep['id'])}</guid>
      <pubDate>{pub}</pubDate>
      <itunes:duration>{_fmt_duration(ep['duration'])}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
      <itunes:episodeType>full</itunes:episodeType>
    </item>""")

    cat_xml = "\n".join(
        f'    <itunes:category text="{escape(c)}"/>' for c in config.categories()
    )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{escape(config.show_title())}</title>
    <link>{escape(config.public_base())}</link>
    <atom:link href="{escape(self_url)}" rel="self" type="application/rss+xml"/>
    <description>{escape(config.show_description())}</description>
    <language>{escape(config.language())}</language>
    <copyright>{escape(config.copyright_line())}</copyright>
    <lastBuildDate>{now_rfc}</lastBuildDate>
    <generator>paper-radio</generator>
    <itunes:author>{escape(config.show_author())}</itunes:author>
    <itunes:summary>{escape(config.show_description())}</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:owner>
      <itunes:name>{escape(config.owner_name())}</itunes:name>
      <itunes:email>{escape(config.owner_email())}</itunes:email>
    </itunes:owner>
    <itunes:image href="{escape(cover_url)}"/>
    <itunes:explicit>{'true' if config.explicit() else 'false'}</itunes:explicit>
{cat_xml}
{chr(10).join(items)}
  </channel>
</rss>
"""
    return xml


def regenerate_feed(store):
    p = store_paths(store)
    episodes = load_episodes(store)
    xml = build_feed_xml(store, episodes)
    _atomic_write(p["feed"], xml)
    return p["feed"]


# ---------------------------------------------------------------------------
# Add / update an episode
# ---------------------------------------------------------------------------
def add_episode(store, mp3_path, title, description, source_url,
                duration=None, pubdate=None):
    p = ensure_store(store)
    if not os.path.isfile(mp3_path):
        raise SystemExit(f"error: mp3 not found: {mp3_path}")

    ep_id = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
    dest = os.path.join(p["audio"], f"{ep_id}.mp3")

    with open(mp3_path, "rb") as src:
        payload = src.read()
    _atomic_write(dest, payload, mode="wb")

    size_bytes = os.path.getsize(dest)
    if duration is None:
        duration = _mp3_duration_seconds(dest)

    episodes = load_episodes(store)
    existing = next((e for e in episodes if e["id"] == ep_id), None)
    tz = ZoneInfo(config.timezone())
    if pubdate is not None:
        pubdate_ts = pubdate
    elif existing is not None:
        pubdate_ts = existing["pubdate_ts"]
    else:
        pubdate_ts = int(datetime.now(tz).timestamp())

    record = {
        "id": ep_id,
        "title": title,
        "description": description,
        "source_url": source_url,
        "bytes": size_bytes,
        "duration": int(duration),
        "pubdate_ts": pubdate_ts,
    }
    if existing is not None:
        episodes = [record if e["id"] == ep_id else e for e in episodes]
        action = "updated"
    else:
        episodes.append(record)
        action = "added"

    _atomic_write(p["episodes"], json.dumps(episodes, indent=2, ensure_ascii=False))
    regenerate_feed(store)

    token = get_token(store)
    base = f"{config.public_base()}/podcast/{token}"
    return {
        "action": action,
        "id": ep_id,
        "title": title,
        "bytes": size_bytes,
        "duration": _fmt_duration(duration),
        "enclosure": f"{base}/ep/{ep_id}.mp3",
        "feed_url": f"{base}/feed.xml",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_pubdate(s):
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(config.timezone()))
    return int(dt.timestamp())


def main():
    ap = argparse.ArgumentParser(description="Add an episode to the private podcast feed.")
    ap.add_argument("--mp3", help="path to the episode mp3")
    ap.add_argument("--title", help="episode title")
    ap.add_argument("--description", default="", help="episode description")
    ap.add_argument("--source-url", dest="source_url", help="source paper URL — dedupe key")
    ap.add_argument("--duration", type=int, default=None, help="override duration in seconds")
    ap.add_argument("--pubdate", default=None, help='override pubdate "YYYY-MM-DD HH:MM"')
    ap.add_argument("--store", default=None, help="store dir (default: PAPER_RADIO_STORE)")
    ap.add_argument("--list", action="store_true", help="list episodes and exit")
    ap.add_argument("--feed-url", action="store_true", help="print feed URL and exit")
    ap.add_argument("--init", action="store_true", help="just create store + token + empty feed")
    args = ap.parse_args()

    store = args.store or config.store_dir()

    if args.feed_url:
        ensure_store(store)
        regenerate_feed(store)
        print(feed_url(store))
        return

    if args.init:
        ensure_store(store)
        regenerate_feed(store)
        print(f"store ready at {store}")
        print(f"feed url: {feed_url(store)}")
        return

    if args.list:
        tz = ZoneInfo(config.timezone())
        for e in sorted(load_episodes(store), key=lambda x: x["pubdate_ts"], reverse=True):
            when = datetime.fromtimestamp(e["pubdate_ts"], tz).strftime("%Y-%m-%d %H:%M")
            print(f"[{e['id']}] {when}  {_fmt_duration(e['duration']):>7}  {e['title']}")
        return

    missing = [f for f in ("mp3", "title", "source_url") if not getattr(args, f)]
    if missing:
        ap.error("--mp3, --title and --source-url are required to add an episode")

    pubdate_ts = _parse_pubdate(args.pubdate) if args.pubdate else None
    result = add_episode(
        store, args.mp3, args.title, args.description, args.source_url,
        duration=args.duration, pubdate=pubdate_ts,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
