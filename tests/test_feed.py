"""
Regression + validity tests for the Paper Radio feed server.

The headline test is `test_feed_head_returns_200`: Apple Podcasts' "Follow a
Show by URL" sends a HEAD preflight to feed.xml before it will GET it. When the
route only allowed GET, HEAD returned 405 and Apple silently refused to add the
show. This test locks HEAD=200 so that bug cannot come back.

Run:  pytest -q   (from the repo root)
"""
import os
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient


ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Isolated store + a published episode, then a TestClient over the app."""
    store = tmp_path / "podcast"
    monkeypatch.setenv("PAPER_RADIO_STORE", str(store))
    monkeypatch.setenv("PAPER_RADIO_PUBLIC_BASE", "https://example.test")
    monkeypatch.setenv("PAPER_RADIO_SHOW_TITLE", "Test Radio")
    monkeypatch.setenv("PAPER_RADIO_OWNER_EMAIL", "owner@example.test")

    # Build a tiny valid mp3 (ID3 + one silent-ish CBR frame is overkill; a
    # minimal MPEG1-L3 128k/44.1k frame header is enough for the pure parser).
    from paper_radio import publish_episode

    mp3 = tmp_path / "ep.mp3"
    frame = bytes([0xFF, 0xFB, 0x90, 0x00])  # MPEG1 L3, 128kbps, 44.1kHz, no pad
    mp3.write_bytes(frame + b"\x00" * 4096)

    publish_episode.add_episode(
        str(store), str(mp3),
        title="Attention Is All You Need",
        description="A deep dive.",
        source_url="https://arxiv.org/abs/1706.03762",
        duration=123,
    )

    token = publish_episode.get_token(str(store))

    # Import AFTER env is set so config picks up the store.
    from paper_radio.server import app

    return TestClient(app), token


def test_feed_head_returns_200(client):
    """THE regression: Apple sends HEAD before GET. It must be 200, not 405."""
    tc, token = client
    resp = tc.head(f"/podcast/{token}/feed.xml")
    assert resp.status_code == 200, (
        f"HEAD feed.xml must be 200 for Apple 'Follow by URL'; got {resp.status_code}"
    )


def test_cover_and_episode_head_ok(client):
    """HEAD on cover + episode enclosure must also succeed (200 or 404 if absent)."""
    tc, token = client
    # episode exists -> 200; cover was never written -> 404 (both non-405)
    ep = tc.head(f"/podcast/{token}/ep/32b9d4c781c8.mp3")
    assert ep.status_code in (200, 404)
    assert tc.head(f"/podcast/{token}/cover.jpg").status_code in (200, 404)


def test_feed_get_is_valid_rss(client):
    """The feed parses as RSS 2.0 with the channel elements Apple requires."""
    tc, token = client
    resp = tc.get(f"/podcast/{token}/feed.xml")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["content-type"]

    root = ET.fromstring(resp.content)
    assert root.tag == "rss"
    assert root.attrib.get("version") == "2.0"
    channel = root.find("channel")
    assert channel is not None
    for tag in ("title", "link", "description"):
        assert channel.find(tag) is not None, f"channel missing <{tag}>"
    # iTunes owner/email + image are required by Apple's directory
    assert channel.find(f"{{{ITUNES}}}owner") is not None
    assert channel.find(f"{{{ITUNES}}}image") is not None

    items = channel.findall("item")
    assert len(items) == 1
    item = items[0]
    enclosure = item.find("enclosure")
    assert enclosure is not None
    assert enclosure.attrib["type"] == "audio/mpeg"
    assert int(enclosure.attrib["length"]) > 0
    assert item.find("guid") is not None
    assert item.find("pubDate") is not None


def test_bad_token_is_404_not_403(client):
    """Wrong token must 404 (path leaks nothing), never 403."""
    tc, _ = client
    assert tc.get("/podcast/deadbeef/feed.xml").status_code == 404
    assert tc.head("/podcast/deadbeef/feed.xml").status_code == 404


def test_episode_range_request(client):
    """Enclosure honours Range (206 + Content-Range) so apps can seek."""
    tc, token = client
    r = tc.get(f"/podcast/{token}/ep/32b9d4c781c8.mp3", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["Content-Range"].startswith("bytes 0-99/")
    assert len(r.content) == 100
