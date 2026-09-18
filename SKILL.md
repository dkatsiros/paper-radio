---
name: paper-radio-setup
description: >-
  Set up "Paper Radio" for a new user end-to-end — a private podcast feed of
  NotebookLM audio deep-dives of research papers. Use when a user wants their
  reading backlog (arXiv/PDFs) turned into a podcast they subscribe to in Apple
  Podcasts / Pocket Casts / Overcast, self-hosted on their own machine and
  their own free Google/NotebookLM account. This skill interviews the user,
  then installs, authenticates, and ships their first episode.
---

# Paper Radio — DIY setup skill (for a coding agent)

You are a coding agent (Claude Code, Codex, or similar) setting up Paper Radio
for a **new user**. Paper Radio turns papers into a private podcast RSS feed via
NotebookLM audio overviews. Your job: interview them, then drive the install,
auth, and first episode to completion on **their** machine and **their** Google
account. It runs for €0 beyond their host.

Read `README.md` in this repo first — it has the architecture and every command.
This skill is the *interactive setup runbook* layered on top.

## Step 0 — Interview the user (ask, then adapt)

Ask these before touching anything. Don't assume; the answers change the plan.

1. **Sources** — "What papers do you want turned into audio? A specific list, an
   arXiv category/author you follow, or ad-hoc URLs you'll paste each time?"
   → determines whether you set up a cron/queue (Step 7) or leave it manual.
2. **Always-on host** — "Do you have an always-on machine with a public HTTPS
   URL to serve the feed? (VPS, home server + tunnel like Cloudflare Tunnel /
   Tailscale Funnel, etc.)" A podcast feed must be reachable 24/7 by the app.
   → If **no**, stop and help them get one (cheapest: a $5 VPS, or a home box +
   Cloudflare Tunnel). The generator can run anywhere, but the *feed* must be
   always-reachable.
3. **Podcast app** — "Which app will you listen in — Apple Podcasts, Pocket
   Casts, or Overcast?" (If they say Spotify, explain the caveat: the listener
   app can't add a raw RSS URL; it requires public submission via Spotify for
   Creators, so it isn't private. Steer them to Apple/Pocket Casts/Overcast.)
4. **Show identity** — title, author name, owner email (Apple needs a real
   email for verification), language, categories.
5. **Google/NotebookLM** — confirm they have a Google account with NotebookLM
   access and can do a one-time browser login on some machine.

## Step 1 — Install
On the always-on host:
```bash
git clone https://github.com/dkatsiros/paper-radio.git && cd paper-radio
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# ffmpeg is required: apt install -y ffmpeg  (or brew install ffmpeg)
```
Verify: `ffmpeg -version` and `notebooklm --version` both succeed.

## Step 2 — Configure
`cp .env.example .env`, then fill it from the interview answers. Minimum:
`PAPER_RADIO_PUBLIC_BASE` (their HTTPS base), `PAPER_RADIO_STORE` (a writable
dir), and the show metadata. Confirm each value back to the user before saving.
**Never** put secrets anywhere but `.env`; confirm `.env` is gitignored.

## Step 3 — NotebookLM auth (the one tricky part)
```bash
notebooklm login          # opens a browser → user logs into THEIR Google account
```
Saves `~/.notebooklm/profiles/default/storage_state.json` (0600).
- **Headless host?** Have the user run `notebooklm login` on their laptop, then
  copy `storage_state.json` to the same path on the host (scp). Or
  `notebooklm login --browser chrome` over a remote display.
- Smoke-test auth WITHOUT spending a generation:
  ```bash
  python -m paper_radio.generate https://arxiv.org/abs/1706.03762 --dry-run
  ```
  Expect "Auth OK. Notebook created + source added." If it errors, auth isn't
  set up — fix before continuing.

## Step 4 — Initialise the feed + serve it
```bash
python -m paper_radio.publish_episode --init   # prints the PRIVATE feed URL
```
Save that URL for the user (it's their credential — tell them to keep it
private). Optionally add a square `cover.jpg` to the store dir.
Bring up the server and put it behind their HTTPS proxy:
```bash
uvicorn paper_radio.server:app --host 0.0.0.0 --port 8000
```
(Prefer a systemd unit / supervisor so it survives reboots — offer to write one.)
Verify from OUTSIDE the box:
```bash
curl -sI https://<their-host>/podcast/<token>/feed.xml   # must be 200, incl. HEAD
```

## Step 5 — First episode
```bash
python -m paper_radio.run <paper-url> --title "..." --description "..."
```
~3–10 min. Confirms with feed + enclosure URLs. Play the enclosure URL once to
confirm audio is real.

## Step 6 — Subscribe (walk them through their chosen app)
- **Apple Podcasts:** Library → ••• → *Follow a Show by URL…* → paste feed URL.
  (Apple sends a HEAD preflight first; this repo's routes handle it — don't let
  anyone "optimize" HEAD away.)
- **Pocket Casts:** Profile → Add Podcast → Add by URL.
- **Overcast:** + → Add URL.

## Step 7 — Automate (only if they wanted it in Step 0)
Set up a cron/queue that feeds paper URLs into `python -m paper_radio.run`.
Keep a `queue.txt` or wire their source (an arXiv RSS filter, a "read later"
list, etc.). Log to a file. Confirm the schedule with the user.

## Step 8 — Verify before declaring done
- `curl -sI …/feed.xml` returns **200** for both GET and HEAD.
- `pytest -q` passes (locks the HEAD regression + RSS validity).
- The show appears in the user's app with the episode playable.
Report exactly what's running (server process/unit, cron if any) and the private
feed URL. Do not claim success until the episode plays in their app.

## Guardrails
- Everything runs on the USER's account and host. Don't exfiltrate their feed
  URL, token, or `storage_state.json`.
- Don't commit `.env`, `data/`, `token.txt`, or `storage_state.json`.
- If they lack an always-on public host, that's the real blocker — solve that
  first; the rest is easy.
