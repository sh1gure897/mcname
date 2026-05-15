"""Async Minecraft Java Edition username availability checker.

This is the main entry point and async engine. It enumerates scored candidate
names (see :mod:`wordlist`), queries the Mojang profile API concurrently with a
bounded semaphore, persists progress to a JSON checkpoint so interrupted runs
resume cleanly, and reports available names both to disk and to a Discord
webhook (see :mod:`notifier`).

Mojang profile API contract used here::

    GET https://api.mojang.com/users/profiles/minecraft/{username}
        404 -> name is available
        200 -> name is taken
        429 -> rate limited; back off exponentially with jitter and retry

Run ``python checker.py --help`` for usage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import signal
from pathlib import Path
from typing import Final, List, Optional, Set

import aiohttp
from tqdm import tqdm

from notifier import DiscordNotifier
from wordlist import NameCandidate, generate_candidates

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MOJANG_PROFILE_URL: Final[str] = (
    "https://api.mojang.com/users/profiles/minecraft/{username}"
)

STATUS_TAKEN: Final[int] = 200
STATUS_NO_CONTENT: Final[int] = 204  # Legacy "available" sentinel; handled too.
STATUS_AVAILABLE: Final[int] = 404
STATUS_RATE_LIMITED: Final[int] = 429

DEFAULT_LENGTHS: Final[List[int]] = [3, 4]
DEFAULT_CONCURRENCY: Final[int] = 10
DEFAULT_DELAY_SECONDS: Final[float] = 0.2

# Exponential backoff tuning for HTTP 429 responses.
MAX_RETRIES: Final[int] = 6
BACKOFF_BASE_SECONDS: Final[float] = 1.0
BACKOFF_FACTOR: Final[float] = 2.0
BACKOFF_MAX_SECONDS: Final[float] = 60.0
BACKOFF_JITTER_SECONDS: Final[float] = 0.5

# Per-request network timeout.
REQUEST_TIMEOUT_SECONDS: Final[float] = 15.0

# Flush the checkpoint to disk every N processed names. Frequent enough to lose
# almost nothing on a crash, rare enough to avoid hammering the filesystem.
CHECKPOINT_INTERVAL: Final[int] = 50

CHECKPOINT_PATH: Final[Path] = Path("checkpoint.json")
RESULTS_PATH: Final[Path] = Path("available_names.txt")
LOG_FILE_PATH: Final[Path] = Path("checker.log")

LOGGER: Final = logging.getLogger("checker")


def configure_logging() -> None:
    """Configure root logging: INFO to the console, DEBUG to a rotating-ish file.

    A single file handler at DEBUG captures the full request trail for
    post-mortem debugging while the console stays readable at INFO.
    """
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))

    file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )

    LOGGER.addHandler(console_handler)
    LOGGER.addHandler(file_handler)


class Checkpoint:
    """Persistent record of which names have already been checked.

    The set of checked names is serialized to JSON as a list. On resume, the
    engine skips any candidate already present here, so a restarted run does no
    redundant network work.

    Attributes:
        path: File the checkpoint is read from / written to.
        checked: Set of usernames that have been fully processed.
    """

    def __init__(self, path: Path) -> None:
        """Initialize and eagerly load any existing checkpoint.

        Args:
            path: Location of the checkpoint JSON file.
        """
        self.path = path
        self.checked: Set[str] = set()
        self._dirty_since_save = 0
        self._load()

    def _load(self) -> None:
        """Load checked names from disk if a valid checkpoint exists.

        A missing or corrupt checkpoint is treated as "start fresh" rather than
        a fatal error, since a damaged file should not block a new run.
        """
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.checked = set(raw.get("checked", []))
            LOGGER.info(
                "Resumed checkpoint: %d names already checked.", len(self.checked)
            )
        except (json.JSONDecodeError, OSError) as exc:
            LOGGER.warning("Could not read checkpoint (%s); starting fresh.", exc)
            self.checked = set()

    def mark(self, name: str) -> None:
        """Record a name as checked and flush periodically.

        Args:
            name: Username that has just been processed.
        """
        self.checked.add(name)
        self._dirty_since_save += 1
        if self._dirty_since_save >= CHECKPOINT_INTERVAL:
            self.save()

    def save(self) -> None:
        """Atomically persist the checked set to disk.

        Writes to a temporary file and renames it so a crash mid-write cannot
        leave a half-written, unparseable checkpoint.
        """
        temp_path = self.path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(
                json.dumps({"checked": sorted(self.checked)}), encoding="utf-8"
            )
            temp_path.replace(self.path)
            self._dirty_since_save = 0
        except OSError as exc:
            LOGGER.warning("Failed to save checkpoint: %s", exc)


class NameChecker:
    """Async engine that scores, checks, and reports username availability.

    Attributes:
        concurrency: Maximum number of in-flight Mojang requests.
        base_delay: Base politeness delay applied before each request.
    """

    def __init__(
        self,
        webhook_url: str,
        lengths: List[int],
        concurrency: int,
        base_delay: float,
    ) -> None:
        """Initialize the engine.

        Args:
            webhook_url: Discord webhook URL for hit notifications.
            lengths: Username lengths to enumerate.
            concurrency: Max parallel requests (bounds the semaphore).
            base_delay: Base per-request delay in seconds.
        """
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

    def request_stop(self) -> None:
        """Signal a graceful shutdown after in-flight work drains."""
        self._stop_event.set()

    async def _backoff_sleep(self, attempt: int) -> None:
        """Sleep for an exponentially increasing, jittered backoff interval.

        Args:
            attempt: Zero-based retry attempt number.
        """
        delay = min(
            BACKOFF_BASE_SECONDS * (BACKOFF_FACTOR**attempt),
            BACKOFF_MAX_SECONDS,
        )
        # Full jitter avoids a thundering herd of synchronized retries.
        delay += random.uniform(0.0, BACKOFF_JITTER_SECONDS)
        LOGGER.debug("Rate limited; backing off %.2fs (attempt %d).", delay, attempt)
        await asyncio.sleep(delay)

    async def _query_mojang(
        self, session: aiohttp.ClientSession, name: str
    ) -> Optional[bool]:
        """Query the Mojang API for a single name, retrying on rate limits.

        Args:
            session: Shared aiohttp session.
            name: Username to check.

        Returns:
            ``True`` if available, ``False`` if taken, or ``None`` if the name
            could not be determined after exhausting retries.
        """
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

                    # Any other status (5xx, etc.) is transient; retry with the
                    # same backoff schedule rather than dropping the name.
                    LOGGER.debug(
                        "Unexpected status %d for '%s'; retrying.", status, name
                    )
                    await self._backoff_sleep(attempt)
            except (aiohttp.ClientError, TimeoutError) as exc:
                LOGGER.debug("Request error for '%s': %s; retrying.", name, exc)
                await self._backoff_sleep(attempt)

        LOGGER.warning("Giving up on '%s' after %d retries.", name, MAX_RETRIES)
        return None

    async def _record_available(self, candidate: NameCandidate) -> None:
        """Append an available name to disk and fire the Discord notification.

        Args:
            candidate: The candidate that was found available.
        """
        async with self._results_lock:
            with RESULTS_PATH.open("a", encoding="utf-8") as handle:
                handle.write(f"{candidate.name}\n")

        LOGGER.info(
            "AVAILABLE: %s (len=%d, score=%d)",
            candidate.name,
            candidate.length,
            candidate.score,
        )

    async def _process_candidate(
        self,
        session: aiohttp.ClientSession,
        notifier: DiscordNotifier,
        candidate: NameCandidate,
        progress: tqdm,
    ) -> None:
        """Check one candidate end-to-end under the concurrency semaphore.

        Args:
            session: Shared aiohttp session.
            notifier: Discord notifier for hits.
            candidate: The candidate to check.
            progress: Progress bar to update.
        """
        async with self._semaphore:
            if self._stop_event.is_set():
                return

            # A small base delay plus jitter spreads requests out so we are a
            # polite API citizen and trip the rate limiter less often.
            await asyncio.sleep(self.base_delay + random.uniform(0.0, self.base_delay))
            is_available = await self._query_mojang(session, candidate.name)

        self._checkpoint.mark(candidate.name)
        self._checked_this_run += 1

        if is_available:
            self._hits += 1
            await self._record_available(candidate)
            await notifier.notify_available(candidate.name, candidate.score)

        progress.update(1)
        progress.set_postfix(hits=self._hits, refresh=False)

    async def run(self) -> None:
        """Drive the full scan: generate, filter, check, and report.

        The candidate stream is materialized lazily; names already present in
        the checkpoint are skipped before any task is scheduled.
        """
        all_candidates = [
            candidate
            for candidate in generate_candidates(self._lengths)
            if candidate.name not in self._checkpoint.checked
        ]
        total = len(all_candidates)
        LOGGER.info(
            "Scanning %d candidate name(s) for length(s) %s.",
            total,
            self._lengths,
        )

        connector = aiohttp.TCPConnector(limit=self.concurrency)
        async with aiohttp.ClientSession(connector=connector) as session:
            notifier = DiscordNotifier(self._webhook_url, session)
            with tqdm(total=total, unit="name", desc="Checking") as progress:
                tasks = [
                    asyncio.create_task(
                        self._process_candidate(
                            session, notifier, candidate, progress
                        )
                    )
                    for candidate in all_candidates
                ]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    # Persist whatever progress we made even on Ctrl-C / error.
                    self._checkpoint.save()

        LOGGER.info(
            "Done. Checked %d this run, %d available name(s) found.",
            self._checked_this_run,
            self._hits,
        )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional explicit argument vector (used by tests).

    Returns:
        Parsed argument namespace.
    """
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


def _install_signal_handlers(checker: NameChecker) -> None:
    """Wire SIGINT/SIGTERM to a graceful stop where the platform supports it.

    Windows' asyncio loop does not implement ``add_signal_handler``; there the
    default ``KeyboardInterrupt`` path still triggers the checkpoint save in the
    ``finally`` block, so this is a best-effort enhancement only.

    Args:
        checker: The engine whose shutdown should be requested.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, checker.request_stop)
        except (NotImplementedError, AttributeError):
            LOGGER.debug("Signal %s not installable on this platform.", sig)


async def _main_async(args: argparse.Namespace) -> None:
    """Async entry point.

    Args:
        args: Parsed CLI arguments.
    """
    checker = NameChecker(
        webhook_url=args.webhook,
        lengths=args.length,
        concurrency=args.concurrency,
        base_delay=args.delay,
    )
    _install_signal_handlers(checker)
    await checker.run()


def main() -> None:
    """Synchronous console entry point."""
    configure_logging()
    args = parse_args()
    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user; progress saved to checkpoint.")


if __name__ == "__main__":
    main()
