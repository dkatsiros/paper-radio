#!/usr/bin/env python3
"""
generate.py — paper URL/PDF -> NotebookLM Audio Overview -> mp3

Drives the `notebooklm` CLI (notebooklm-py) headlessly against YOUR own Google
account, then transcodes the downloaded m4a to mp3 with ffmpeg (podcast apps
want mp3 enclosures).

    python -m paper_radio.generate https://arxiv.org/abs/1706.03762 \
        --title "Attention Is All You Need"

Auth (one-time, on the machine that runs this):
    notebooklm login
  → opens a browser, saves storage_state.json under
    ~/.notebooklm/profiles/<profile>/storage_state.json (0600).
  On a headless box, run `notebooklm login` once on a desktop and copy that
  file over, or use --browser chrome with an X/remote display.

Config (env / .env): PAPER_RADIO_NOTEBOOKLM_BIN, PAPER_RADIO_FFMPEG_BIN,
PAPER_RADIO_AUDIO_FORMAT (deep-dive|brief|debate), PAPER_RADIO_MP3_BITRATE.

Prints the absolute path of the final mp3 on the last stdout line (easy to
pipe into publish_episode).
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import config


def _nlm(*args):
    result = subprocess.run(
        [config.notebooklm_bin(), *args], capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[ERROR] notebooklm exited {result.returncode}", file=sys.stderr)
        print(result.stderr.strip(), file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip(), result.stderr.strip()


def _nlm_json(*args):
    out, _ = _nlm(*args, "--json")
    return json.loads(out)


def _slug(title: str) -> str:
    return re.sub(r"[^\w-]", "-", title.lower())[:40].strip("-")


def _to_mp3(m4a_path: Path, bitrate: str) -> Path:
    mp3_path = m4a_path.with_suffix(".mp3")
    cmd = [
        config.ffmpeg_bin(), "-y", "-i", str(m4a_path),
        "-codec:a", "libmp3lame", "-b:a", bitrate, str(mp3_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("[ERROR] ffmpeg m4a->mp3 failed", file=sys.stderr)
        print(proc.stderr.strip()[-2000:], file=sys.stderr)
        sys.exit(1)
    return mp3_path


def generate(source: str, title: str = None, fmt: str = None,
             out_dir: str = None, keep_m4a: bool = False, dry_run: bool = False):
    fmt = fmt or config.audio_format()
    out = Path(out_dir or (Path.cwd() / "output"))
    out.mkdir(parents=True, exist_ok=True)

    ts_label = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = title or f"Paper {ts_label}"

    print(f"[1/4] Creating notebook: {title!r}")
    nb = _nlm_json("create", title, "--use")
    nb_id = nb.get("id") or nb.get("notebook_id")
    print(f"      id={nb_id}")

    print(f"[2/4] Adding source: {source}")
    _nlm("source", "add", source)
    print("      Done.")

    if dry_run:
        print("\n[DRY-RUN] Auth OK. Notebook created + source added. Skipping audio generation.")
        return None

    print(f"[3/4] Generating audio overview ({fmt}) -- typically 3-10 min ...")
    gen_out, _ = _nlm("generate", "audio", "--format", fmt, "--wait", "--timeout", "1200")
    print(f"      {gen_out[:200]}")

    ts_file = datetime.now().strftime("%Y%m%d-%H%M%S")
    m4a_file = out / f"{_slug(title)}-{ts_file}.m4a"
    print(f"[4/4] Downloading -> {m4a_file}")
    _nlm("download", "audio", str(m4a_file))

    mp3_file = _to_mp3(m4a_file, config.mp3_bitrate())
    if not keep_m4a:
        try:
            m4a_file.unlink()
        except OSError:
            pass

    print(f"\n[OK] {mp3_file}")
    print(str(mp3_file))  # last line = absolute mp3 path
    return str(mp3_file)


def main():
    ap = argparse.ArgumentParser(description="Paper URL/PDF -> NotebookLM -> mp3")
    ap.add_argument("source", help="ArXiv/paper URL or local PDF path")
    ap.add_argument("--title", help="Notebook title (default: Paper YYYY-MM-DD HH:MM)")
    ap.add_argument("--format", dest="fmt",
                    choices=["deep-dive", "brief", "debate"], default=None)
    ap.add_argument("--out", default=None, help="output dir (default ./output)")
    ap.add_argument("--keep-m4a", action="store_true", help="keep the intermediate m4a")
    ap.add_argument("--dry-run", action="store_true",
                    help="Smoke-test auth: create notebook + add source, then stop")
    args = ap.parse_args()
    generate(args.source, title=args.title, fmt=args.fmt, out_dir=args.out,
             keep_m4a=args.keep_m4a, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
