# 📻 Paper Radio

**Turn research papers into a private podcast you actually listen to.**

Paper Radio is an unattended pipeline: point it at an arXiv paper (or any PDF),
and it produces a NotebookLM two-host "deep dive" audio overview, then publishes
it to a **private podcast RSS feed** you subscribe to in Apple Podcasts, Pocket
Casts, or Overcast. It runs headless on an always-on machine against **your own**
Google/NotebookLM account, so it costs **€0** beyond the box it runs on.

Papers pile up unread. Audio doesn't. This turns the reading backlog into a
commute.

---

## How it works

```
  arXiv URL / PDF
        │
        ▼
┌───────────────────┐   your own Google account, headless
│  generate.py      │   (notebooklm-py CLI, MIT)
│  NotebookLM       │──► "deep dive" 2-host audio  ──►  .m4a
│  Audio Overview   │
└───────────────────┘
        │  ffmpeg  (.m4a → .mp3)
        ▼
┌───────────────────┐
│ publish_episode.py│   copies mp3 into the store, dedupes by source URL,
│  RSS 2.0 + iTunes │   regenerates feed.xml atomically
└───────────────────┘
        │   writes to  <store>/{feed.xml, episodes.json, audio/*.mp3}
        ▼
┌───────────────────┐   read-only FastAPI, no auth — privacy via an
│  server.py        │   unguessable 32-char token in the URL path
│  /podcast/<tok>/  │   GET|HEAD feed.xml · ep/<id>.mp3 (Range) · cover.jpg
└───────────────────┘
        │   https://your-host/podcast/<token>/feed.xml
        ▼
   Apple Podcasts / Pocket Casts / Overcast  ← you subscribe here
```

`run.py` ties generate + publish into one command for cron/unattended use.

### Why a token in the URL instead of a login?

Podcast apps can't authenticate — they just fetch a URL. So the feed is served
with **no auth**; privacy comes from a 32-char random token embedded in the path
(`/podcast/<token>/…`). A wrong token returns **404** (not 403) so the path
leaks nothing. The content is public papers rendered to audio — no secrets — so
obscurity of the token is a sufficient boundary. Keep the feed URL private and
it stays your private show.

---

## Self-host guide

### Prerequisites
- An **always-on machine** with a public HTTPS URL (a small VPS, a home server
  behind a reverse proxy/tunnel, etc.). This serves the feed and runs generation.
- **Your own Google account** with NotebookLM access.
- `python3.10+`, `ffmpeg`, and `pip`.

### 1. Install
```bash
git clone https://github.com/dkatsiros/paper-radio.git
cd paper-radio
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt install -y ffmpeg   # or: brew install ffmpeg
```

### 2. Configure
```bash
cp .env.example .env
# edit .env — at minimum set PAPER_RADIO_PUBLIC_BASE, PAPER_RADIO_STORE,
# and the show metadata (title, author, owner email).
```

### 3. One-time NotebookLM auth
```bash
notebooklm login          # opens a browser, log into your Google account
# saves ~/.notebooklm/profiles/default/storage_state.json (chmod 0600)
```
**Headless box?** Run `notebooklm login` once on a desktop, then copy that
`storage_state.json` to the same path on the server (or use
`notebooklm login --browser chrome` over a remote display). Smoke-test without
burning a generation:
```bash
python -m paper_radio.generate https://arxiv.org/abs/1706.03762 --dry-run
```

### 4. Initialise the feed store
```bash
python -m paper_radio.publish_episode --init
# prints your private feed URL — this is what you subscribe to. Keep it secret.
```
Optionally drop a square cover image at `<store>/cover.jpg` (1400–3000 px,
required by Apple if you ever submit to the directory).

### 5. Make your first episode
```bash
python -m paper_radio.run https://arxiv.org/abs/1706.03762 \
    --title "Attention Is All You Need" \
    --description "The Transformer paper."
# generation takes ~3–10 min; prints the feed + enclosure URLs when done.
```
Re-running the same paper URL **updates** that episode in place (idempotent).

### 6. Host the feed
```bash
uvicorn paper_radio.server:app --host 0.0.0.0 --port 8000
```
Put it behind your HTTPS reverse proxy so `PAPER_RADIO_PUBLIC_BASE` resolves.
Or mount `paper_radio.server.router` into an existing FastAPI app.

### 7. Automate (optional)
Cron a paper source, or wire it to whatever picks papers for you:
```cron
# every morning, deep-dive the latest paper on your list
0 7 * * *  cd /srv/paper-radio && . .venv/bin/activate && \
           python -m paper_radio.run "$(head -1 /srv/paper-radio/queue.txt)" >> /var/log/paper-radio.log 2>&1
```

---

## Subscribe

Your private feed URL is `https://<your-host>/podcast/<token>/feed.xml`
(from step 4). Then:

### Apple Podcasts  *(first-class)*
1. Open **Apple Podcasts**.
2. **Library** tab → top-right **•••** menu → **Follow a Show by URL…**
   (on Mac: **File → Follow a Show by URL…**).
3. Paste the feed URL, tap **Follow**.

> Apple sends a **HEAD** request to `feed.xml` *before* it will GET it. If the
> server answered 405 to HEAD, Apple silently refused to add the show — this was
> a real bug. Paper Radio's routes answer HEAD with 200, and a regression test
> (`tests/test_feed.py`) locks it so it can't come back.

### Pocket Casts
Profile → **Add Podcast** → **Add by URL** (or **Add a URL** on mobile) → paste
the feed URL.

### Overcast
**+** (top right) → **Add URL** → paste the feed URL.

### Spotify — the honest caveat
The Spotify **listener** app cannot add a raw RSS URL. To get it into Spotify
you must claim the show via **[Spotify for Creators](https://creators.spotify.com/)**
(Add your podcast → paste the RSS URL → verify via the code Spotify emails to
`PAPER_RADIO_OWNER_EMAIL`). That also lists it publicly, so it's not really
"private" anymore. For a private feed, use Apple/Pocket Casts/Overcast.

---

## Layout

| Path | What |
|---|---|
| `paper_radio/config.py` | all config via env / `.env` — nothing user-specific in code |
| `paper_radio/generate.py` | paper → NotebookLM audio → m4a → mp3 |
| `paper_radio/publish_episode.py` | RSS 2.0 + iTunes feed writer; atomic + idempotent |
| `paper_radio/server.py` | read-only FastAPI feed server (GET+HEAD, Range) |
| `paper_radio/run.py` | end-to-end generate + publish (for cron) |
| `tests/test_feed.py` | HEAD=200 regression + RSS-validity tests |

## Security notes
- Never commit `.env`, the store `data/`, `token.txt`, or `storage_state.json`
  (all in `.gitignore`). The token **is** the credential to your feed.
- The feed serves only public-paper audio; keep the URL private and it's private.

## License
MIT — see [LICENSE](LICENSE).

---
> **Repo status:** the git remote is set to
> `https://github.com/dkatsiros/paper-radio.git` but **nothing has been pushed
> yet** — waiting on a personal-account GitHub token. See the project page for
> how to grant it.
