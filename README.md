#  Minecraft Name Checker

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Async](https://img.shields.io/badge/async-asyncio%20%2B%20aiohttp-ff69b4.svg)](https://docs.aiohttp.org/)
[![Code style: typed](https://img.shields.io/badge/types-mypy%20clean-2a6db2.svg)](http://mypy-lang.org/)

> An async, resumable Minecraft Java Edition username availability checker with a coolness-scoring engine and Discord notifications.

It enumerates every 3–4 character username, ranks each one with a heuristic
**coolness score (0–100)**, and checks the *best* names first against the
Mojang API — fully asynchronously, with a checkpoint so you can stop and resume
any time.

---

##  Features

- **Async engine** — `asyncio` + `aiohttp`, bounded by an `asyncio.Semaphore`. No threads.
- **Smart scoring** — real-word, pronounceability, Japanese-romaji vibe, clean alphanumeric mix, and more. Best names are checked first.
- **Rate-limit aware** — HTTP 429 triggers exponential backoff with full jitter and automatic retry.
- **Resumable** — atomic JSON checkpoint of every checked name; restarts skip completed work instantly.
- **Real-time results** — available names are appended to `available_names.txt` the moment they're found.
- **Discord notifications** — rich embed per hit (name, length, score, NameMC link, timestamp).
- **Live progress** — `tqdm` bar with totals, throughput, and a running hit counter.
- **Production logging** — INFO to console, full DEBUG trail to `checker.log`.
- **Fully typed** — `from __future__ import annotations`, mypy-clean, Google-style docstrings throughout.

---

##  Installation

```bash
git clone https://github.com/yourname/minecraft-name-checker.git
cd minecraft-name-checker
pip install -r requirements.txt
```

Requires **Python 3.10+**.

---

##  Usage

```bash
# Default: check all 3 and 4 character names
python checker.py --webhook "https://discord.com/api/webhooks/XXX/YYY"

# Only 3-letter names, higher concurrency
python checker.py --webhook "https://discord.com/api/webhooks/XXX/YYY" \
    --length 3 --concurrency 20

# Gentle, polite scan (slower, less likely to be rate limited)
python checker.py --webhook "https://discord.com/api/webhooks/XXX/YYY" \
    --length 3 4 --concurrency 5 --delay 0.5
```

Stop with `Ctrl+C` at any time — progress is flushed to `checkpoint.json`.
Re-run the same command to resume exactly where you left off.

### Standalone Windows executable (no Python required)

```bash
pip install -r requirements-dev.txt
pyinstaller build.spec --clean --noconfirm
```

This produces `dist/mc-name-checker.exe` (~10 MB, self-contained). Run it like
the script:

```bat
mc-name-checker.exe --webhook "https://discord.com/api/webhooks/XXX/YYY" --length 4
```

---

##  Configuration

| Flag            | Type       | Default | Description                                      |
|-----------------|------------|---------|--------------------------------------------------|
| `--webhook`     | `str`      | —       | **Required.** Discord webhook URL for hits.      |
| `--length`      | `int...`   | `3 4`   | One or more username lengths to enumerate.       |
| `--concurrency` | `int`      | `10`    | Max parallel Mojang requests (semaphore bound).  |
| `--delay`       | `float`    | `0.2`   | Base per-request delay in seconds (plus jitter). |

Generated files:

| File                 | Purpose                                            |
|----------------------|----------------------------------------------------|
| `available_names.txt`| Available names, appended live, one per line.      |
| `checkpoint.json`    | Set of already-checked names (for resume).         |
| `checker.log`        | Full DEBUG log trail.                              |

---

##  How the scoring system works

Every candidate is scored once and candidates are explored in
**score-descending order**, so you find the desirable names first. Within a
single score band, order is shuffled to avoid lexical patterns that make rate
limiting more aggressive.

| Criterion                                   | Δ Score |
|---------------------------------------------|--------:|
| Matches an embedded common English word     |   `+40` |
| Pronounceable (vowel present, no long runs) |   `+30` |
| Japanese romaji vibe (`kai`, `ryu`, `ren`…) |   `+20` |
| Clean alphanumeric mix (balanced letters/digits) | `+15` |
| No repeated characters                      |   `+10` |
| Contains an underscore                      |   `-20` |
| All digits                                  |   `-30` |

Final score is clamped to `[0, 100]`. Names that are all-identical
characters, start/end with `_`, or hit the basic offensive blocklist are
**skipped entirely** and never generated.

The word list and phonetic data are **embedded directly in `wordlist.py`** —
no external files, no paid APIs.

---

##  Rate limits & Terms of Service

This tool is provided for **educational and personal use**. Be a good citizen:

- Mojang's API is **rate limited**. Keep `--concurrency` modest and `--delay`
  reasonable. The built-in exponential backoff is a safety net, not a license
  to hammer the endpoint.
- Automated, high-volume querying may violate the
  [Minecraft EULA](https://www.minecraft.net/eula) and Mojang's API usage
  expectations. **You are responsible for how you use this software.**
- Username availability reported by the API is **not a reservation**. A name
  can be claimed by someone else at any moment.

Use responsibly. The authors accept no liability for misuse.

---

##  Contributing

Contributions are welcome!

1. Fork the repo and create a feature branch.
2. Keep the style: full type hints, Google-style docstrings, no magic numbers,
   no bare `except`.
3. Run `mypy .` and ensure it stays clean.
4. Open a PR with a clear description of the change and its motivation.

Ideas: smarter scoring heuristics, additional notifier backends (Slack,
Telegram), configurable alphabets, or a pluggable word list.

---

##  License

MIT — see [`LICENSE`](LICENSE).
