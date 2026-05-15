"""Minecraft Java username availability checker.

Walks the scored candidate list from wordlist.py, hits the Mojang profile
endpoint for each name with a capped number of concurrent requests, writes a
checkpoint as it goes so a killed run can pick up where it left off, and pings
a Discord webhook when something is free.

Mojang endpoint:
    GET https://api.mojang.com/users/profiles/minecraft/{username}
    404 = free, 200 = taken, 429 = slow down

python checker.py --help for the flags.
"""

import argparse
import asyncio
import json
import logging
import random
import signal
from pathlib import Path
from typing import NamedTuple

import aiohttp
from tqdm import tqdm

from notifier import DiscordNotifier
from wordlist import NAME_ALPHABET, NameCandidate, generate_candidates

MOJANG_PROFILE_URL = "https://api.mojang.com/users/profiles/minecraft/{username}"

STATUS_TAKEN = 200
STATUS_NO_CONTENT = 204  # old "available" code, still seen occasionally
STATUS_AVAILABLE = 404
STATUS_RATE_LIMITED = 429

DEFAULT_LENGTHS = [3, 4]
DEFAULT_CONCURRENCY = 10
DEFAULT_DELAY_SECONDS = 0.2

MAX_RETRIES = 6


class BackoffConfig(NamedTuple):
    base: float = 1.0
    factor: float = 2.0
    max_seconds: float = 60.0
    jitter: float = 0.5


BACKOFF = BackoffConfig()

REQUEST_TIMEOUT_SECONDS = 15.0

# How often the checkpoint hits disk. Often enough that a crash barely costs
# anything, not so often that we thrash the disk.
CHECKPOINT_INTERVAL = 50

CHECKPOINT_PATH = Path("checkpoint.json")
RESULTS_PATH = Path("available_names.txt")
LOG_FILE_PATH = Path("checker.log")

log = logging.getLogger("checker")


def configure_logging():
    """Console at INFO, full DEBUG trail in checker.log."""
    log.setLevel(logging.DEBUG)
    log.propagate = False

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))

    logfile = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    logfile.setLevel(logging.DEBUG)
    logfile.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )

    log.addHandler(console)
    log.addHandler(logfile)


class Checkpoint:
    """Tracks which names we've already looked at so reruns don't repeat work."""

    def __init__(self, path: Path):
        self.path = path
        self.checked = set()
        self._dirty_since_save = 0
        self._load()

    def _load(self):
        # Missing or broken checkpoint means "start over", not an error.
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.checked = set(raw.get("checked", []))
            log.info("Resumed checkpoint: %d names already checked.", len(self.checked))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read checkpoint (%s); starting fresh.", exc)
            self.checked = set()

    def mark(self, name: str):
        self.checked.add(name)
        self._dirty_since_save += 1
        if self._dirty_since_save >= CHECKPOINT_INTERVAL:
            self.save()

    def save(self):
        # atomic write
        temp_path = self.path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(
                json.dumps({"checked": sorted(self.checked)}), encoding="utf-8"
            )
            temp_path.replace(self.path)
            self._dirty_since_save = 0
        except OSError as exc:
            log.warning("Failed to save checkpoint: %s", exc)


class NameChecker:
    """Runs the scan: pull candidates, query Mojang, report whatever is open."""

    def __init__(self, webhook_url, lengths, concurrency, base_delay):
        self._webhook_url = webhook_url
        self._lengths = lengths
        self.concurrency = concurrency
        self.base_delay = base_delay

        self._semaphore = asyncio.Semaphore(concurrency)
        self._checkpoint = Checkpoint(CHECKPOINT_PATH)
        self._results_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

        self._hits = 0
        self._checked_this_run = 0

    def request_stop(self):
        """Ask the scan to wind down once in-flight requests finish."""
        self._stop_event.set()

    async def _backoff_sleep(self, attempt: int):
        delay = min(
            BACKOFF.base * (BACKOFF.factor ** attempt),
            BACKOFF.max_seconds,
        )
        # Jitter so a batch of retries doesn't all fire at the same instant.
        delay += random.uniform(0.0, BACKOFF.jitter)
        log.debug("Rate limited; backing off %.2fs (attempt %d).", delay, attempt)
        await asyncio.sleep(delay)

    async def _query_mojang(self, session: aiohttp.ClientSession, name: str):
        """True = free, False = taken, None = gave up after all retries."""
        url = MOJANG_PROFILE_URL.format(username=name)
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)

        for attempt in range(MAX_RETRIES):
            try:
                async with session.get(url, timeout=timeout) as response:
                    status = response.status

                    if status in (STATUS_AVAILABLE, STATUS_NO_CONTENT):
                        return True
                    if status == STATUS_TAKEN:
                        return False
                    if status == STATUS_RATE_LIMITED:
                        await self._backoff_sleep(attempt)
                        continue

                    # 5xx and friends — retry on the same schedule.
                    log.debug("Unexpected status %d for '%s'; retrying.", status, name)
                    await self._backoff_sleep(attempt)
            except (aiohttp.ClientError, TimeoutError) as exc:
                log.debug("Request error for '%s': %s; retrying.", name, exc)
                await self._backoff_sleep(attempt)

        log.warning("Giving up on '%s' after %d retries.", name, MAX_RETRIES)
        return None

    async def _record_available(self, cand: NameCandidate):
        async with self._results_lock:
            with RESULTS_PATH.open("a", encoding="utf-8") as handle:
                handle.write(f"{cand.name}\n")

        log.info(
            "AVAILABLE: %s (len=%d, score=%d)",
            cand.name,
            cand.length,
            cand.score,
        )

    async def _process_candidate(self, session, notifier, cand, progress):
        delay = self.base_delay
        await asyncio.sleep(delay + random.uniform(0.0, delay))
        async with self._semaphore:
            if self._stop_event.is_set():
                return
            available = await self._query_mojang(session, cand.name)

        self._checkpoint.mark(cand.name)
        self._checked_this_run += 1

        if available:
            self._hits += 1
            await self._record_available(cand)
            await notifier.notify_available(cand.name, cand.score)

        progress.update(1)
        progress.set_postfix(hits=self._hits, refresh=False)

    async def _producer(self, queue: asyncio.Queue):
        for cand in generate_candidates(self._lengths):
            if self._stop_event.is_set():
                break
            if cand.name in self._checkpoint.checked:
                continue
            await queue.put(cand)
        for _ in range(self.concurrency):
            await queue.put(None)

    async def _worker(self, queue: asyncio.Queue, session, notifier, progress):
        while True:
            cand = await queue.get()
            if cand is None:
                break
            await self._process_candidate(session, notifier, cand, progress)

    async def run(self):
        log.info("Scanning candidate name(s) for length(s) %s.", self._lengths)

        connector = aiohttp.TCPConnector(limit=self.concurrency)
        async with aiohttp.ClientSession(connector=connector) as session:
            notifier = DiscordNotifier(self._webhook_url, session)
            already_checked = len(self._checkpoint.checked)
            total = max(_count_candidates(self._lengths) - already_checked, 0)
            with tqdm(total=total, unit="name", desc="Checking") as progress:
                queue = asyncio.Queue(maxsize=self.concurrency * 2)
                try:
                    await asyncio.gather(
                        self._producer(queue),
                        *[
                            self._worker(queue, session, notifier, progress)
                            for _ in range(self.concurrency)
                        ],
                    )
                finally:
                    self._checkpoint.save()

        log.info(
            "Done. Checked %d this run, %d available name(s) found.",
            self._checked_this_run,
            self._hits,
        )


def _count_candidates(lengths: list[int]) -> int:
    """Total keyspace size across all requested lengths.

    len(NAME_ALPHABET) ** length, summed. Skippable names are a tiny
    fraction so we don't bother subtracting them — the estimate is close
    enough for a progress bar.
    """
    return sum(len(NAME_ALPHABET) ** n for n in lengths)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Async Minecraft Java Edition username availability checker.",
    )
    parser.add_argument(
        "--webhook",
        required=True,
        help="Discord webhook URL for available-name notifications.",
    )
    parser.add_argument(
        "--length",
        type=int,
        nargs="+",
        default=DEFAULT_LENGTHS,
        metavar="N",
        help="Name length(s) to check (default: 3 4).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Max parallel requests (default: 10).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Base delay between requests in seconds (default: 0.2).",
    )
    return parser.parse_args(argv)


def _install_signal_handlers(checker: "NameChecker"):
    # Windows' asyncio loop has no add_signal_handler; Ctrl-C raises
    # KeyboardInterrupt there and the finally block saves anyway.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, checker.request_stop)
        except (NotImplementedError, AttributeError):
            log.debug("Signal %s not installable on this platform.", sig)


async def _main_async(args):
    checker = NameChecker(
        webhook_url=args.webhook,
        lengths=args.length,
        concurrency=args.concurrency,
        base_delay=args.delay,
    )
    _install_signal_handlers(checker)
    await checker.run()


def main():
    configure_logging()
    args = parse_args()
    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        log.info("Interrupted by user; progress saved to checkpoint.")


if __name__ == "__main__":
    main()
