# Contributing to CutWeave

Thanks for your interest. CutWeave is a young project — the fastest way to help right now is to break it and tell us how.

## Local setup

Prerequisites: Python 3.11+, FFmpeg (with `ffprobe`), and for text layers any CJK font on your system (macOS PingFang / Hiragino, Debian `fonts-noto-cjk` are auto-detected).

```bash
git clone https://github.com/Alfred-Lau/cutweave.git
cd cutweave/engine
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q          # all tests should pass
python scripts/demo_e2e.py          # end-to-end smoke demo
```

## Conventions

- Keep changes small; one PR per topic.
- New engine behavior needs tests in `engine/tests/` (models / compiler command assertions / API integration).
- Compiler changes: assert on the generated FFmpeg command in `test_compiler.py`, not just "it renders".
- Don't commit rendered media or draft data — `.gitignore` already excludes `engine/data/` and `engine/.testdata/`.
- Commits: short imperative subject, e.g. `compiler: clamp PiP scale to canvas`.

## Reporting bugs

Open an issue with:

1. Your OS, Python version, and `ffmpeg -version`
2. The draft JSON that failed (strip any private URLs)
3. The failing request and full response

## Roadmap input

P1+ direction (async task bus, ASR subtitles, workflow interpreter) is sketched in the root README. If you want to pick up one of these, open an issue first so we don't duplicate work.
