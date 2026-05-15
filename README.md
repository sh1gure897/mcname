# Minecraft Name Checker

An async, resumable checker for free Minecraft Java Edition usernames, with a
small scoring engine so the good names get checked first and a Discord webhook
for hits.

It walks every 3-4 character name, gives each one a rough coolness score
(0-100), and checks the best ones first against the Mojang API. Concurrency is
capped, runs are checkpointed, so you can stop and pick up again whenever.

## Features

- Async: `asyncio` + `aiohttp`, requests bounded by an `asyncio.Semaphore`.
- Scoring: real words, pronounceability, a romaji "vibe" bonus, clean
  letter/digit mixes, etc. Best names first.
- Handles 429s with exponential backoff + jitter and retries.
- Resumable: every checked name goes into an atomic JSON checkpoint, so a
  restart skips finished work.
- Hits are appended to `available_names.txt` as soon as they're found.
- Discord webhook embed per hit (name, length, score, NameMC link, time).
- `tqdm` progress bar with a live hit counter.
- INFO to the console, a full DEBUG trail in `checker.log`.

## Install

```bash
git clone https://github.com/yourname/minecraft-name-checker.git
cd minecraft-name-checker
pip install -r requirements.txt
```

Python 3.10+.

## Usage

```bash
# all 3 and 4 char names
python checker.py --webhook "https://discord.com/api/webhooks/XXX/YYY"

# only 3-letter names, more concurrency
python checker.py --webhook "https://discord.com/api/webhooks/XXX/YYY" \
    --length 3 --concurrency 20

# slower, gentler on the API
python checker.py --webhook "https://discord.com/api/webhooks/XXX/YYY" \
    --length 3 4 --concurrency 5 --delay 0.5
```

`Ctrl+C` whenever — progress is flushed to `checkpoint.json`, and re-running
the same command resumes from there.

### Standalone Windows exe (no Python needed)

```bash
pip install -r requirements-dev.txt
pyinstaller build.spec --clean --noconfirm
```

Produces `dist/mc-name-checker.exe` (~10 MB, self-contained). Same args:

```bat
mc-name-checker.exe --webhook "https://discord.com/api/webhooks/XXX/YYY" --length 4
```

## Flags

| Flag            | Type     | Default | Notes                                     |
|-----------------|----------|---------|-------------------------------------------|
| `--webhook`     | `str`    | —       | Required. Discord webhook URL for hits.   |
| `--length`      | `int...` | `3 4`   | One or more name lengths to enumerate.    |
| `--concurrency` | `int`    | `10`    | Max parallel Mojang requests.             |
| `--delay`       | `float`  | `0.2`   | Base per-request delay, seconds (+jitter).|

Files it writes:

| File                  | What                                          |
|-----------------------|-----------------------------------------------|
| `available_names.txt` | Free names, appended live, one per line.      |
| `checkpoint.json`     | Names already checked (used to resume).       |
| `checker.log`         | Full DEBUG log.                               |

## Scoring

Each candidate is scored once, then candidates are walked highest score first.
Names tied on score are shuffled within that band so the request stream isn't
an obvious alphabetical crawl (the rate limiter is harsher on those).

| Criterion                                        | Δ Score |
|--------------------------------------------------|--------:|
| Matches a word in the built-in list              |   `+40` |
| Pronounceable (has a vowel, no long runs)        |   `+30` |
| Romaji vibe (`kai`, `ryu`, `ren`, …)             |   `+20` |
| Clean letter/digit mix                           |   `+15` |
| No repeated characters                           |   `+10` |
| Has an underscore                                |   `-20` |
| All digits                                       |   `-30` |

Final score is clamped to `[0, 100]`. All-identical names, names that start or
end with `_`, and anything hitting the small blocklist are skipped entirely
and never generated.

The word list and romaji data live directly in `wordlist.py` — no external
files, no paid APIs.

## Rate limits & ToS

Educational / personal use. Play nice:

- The Mojang API is rate limited. Keep `--concurrency` modest and `--delay`
  sane. The backoff is a safety net, not an excuse to hammer it.
- High-volume automated querying may run against the
  [Minecraft EULA](https://www.minecraft.net/eula) and Mojang's API
  expectations. How you use this is on you.
- "Available" from the API is not a reservation — someone else can grab a
  name a second later.

No liability for misuse.

## Contributing

PRs welcome.

1. Fork, branch.
2. Keep it simple and readable; no bare `except`.
3. Describe what the change does and why.

Ideas: better scoring heuristics, more notifier backends (Slack, Telegram),
configurable alphabet, pluggable word list.

## License

MIT — see [`LICENSE`](LICENSE).
