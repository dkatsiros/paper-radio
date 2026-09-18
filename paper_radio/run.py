#!/usr/bin/env python3
"""
run.py — end-to-end: paper URL -> NotebookLM mp3 -> published to your feed.

One command that ties generate + publish together:

    python -m paper_radio.run https://arxiv.org/abs/1706.03762 \
        --title "Attention Is All You Need" \
        --description "The Transformer paper."

Runs generate() (NotebookLM -> mp3), then add_episode() (copy into the store,
regenerate feed.xml). Prints the JSON publish result (feed URL + enclosure).

Idempotent by --source-url: re-running the same paper updates the episode in
place. Meant to be called unattended from cron / a task runner on an always-on
host, or by hand.
"""
import argparse
import json
import sys

from . import config
from .generate import generate
from .publish_episode import add_episode


def main():
    ap = argparse.ArgumentParser(description="paper URL -> mp3 -> published feed")
    ap.add_argument("source", help="ArXiv/paper URL or local PDF path (also the dedupe key)")
    ap.add_argument("--title", help="episode + notebook title")
    ap.add_argument("--description", default="", help="episode description")
    ap.add_argument("--format", dest="fmt",
                    choices=["deep-dive", "brief", "debate"], default=None)
    ap.add_argument("--source-url", dest="source_url", default=None,
                    help="override dedupe key / <link> (default: the source arg)")
    ap.add_argument("--out", default=None, help="generation output dir")
    ap.add_argument("--store", default=None, help="feed store dir (default: PAPER_RADIO_STORE)")
    ap.add_argument("--keep-m4a", action="store_true")
    args = ap.parse_args()

    mp3 = generate(args.source, title=args.title, fmt=args.fmt,
                   out_dir=args.out, keep_m4a=args.keep_m4a)
    if not mp3:
        print("generation produced no mp3", file=sys.stderr)
        sys.exit(1)

    store = args.store or config.store_dir()
    source_url = args.source_url or args.source
    result = add_episode(
        store, mp3,
        title=args.title or source_url,
        description=args.description,
        source_url=source_url,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
