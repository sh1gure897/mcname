"""Discord Webhook notifications for available-name hits.

The notifier sends a single rich embed per available name. Network failures are
swallowed and logged as warnings: a flaky webhook must never interrupt or crash
a long-running scan.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Final

import aiohttp

LOGGER: Final = logging.getLogger(__name__)

# Discord embed accent color for a "success" event (green).
EMBED_COLOR_SUCCESS: Final[int] = 0x00FF88

EMBED_TITLE: Final[str] = "🎯 Available Minecraft Name Found!"
NAMEMC_PROFILE_URL: Final[str] = "https://namemc.com/profile/{name}"

# Discord rejects requests that take too long to build; keep the per-send
# timeout tight so a hung webhook cannot stall the scan loop.
WEBHOOK_TIMEOUT_SECONDS: Final[float] = 10.0

# Discord returns 204 No Content on a successfully delivered webhook.
DISCORD_SUCCESS_STATUS: Final[int] = 204


class DiscordNotifier:
    """Sends available-name embeds to a Discord webhook.

    The notifier reuses the caller-provided :class:`aiohttp.ClientSession` so it
    does not own connection lifecycle; this keeps it composable with the main
    scan engine's session.

    Attributes:
        webhook_url: Fully-qualified Discord webhook URL.
    """

    def __init__(self, webhook_url: str, session: aiohttp.ClientSession) -> None:
        """Initialize the notifier.

        Args:
            webhook_url: Discord webhook URL to post embeds to.
            session: Shared aiohttp session used for the POST request.
        """
        self.webhook_url = webhook_url
        self._session = session

    def _build_payload(self, name: str, score: int) -> dict:
        """Build the Discord webhook JSON payload for one available name.

        Args:
            name: The available username.
            score: Coolness score of the name.

        Returns:
            A JSON-serializable dict matching Discord's webhook embed schema.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        profile_url = NAMEMC_PROFILE_URL.format(name=name)

        return {
            "embeds": [
                {
                    "title": EMBED_TITLE,
                    "color": EMBED_COLOR_SUCCESS,
                    "fields": [
                        {"name": "Name", "value": f"`{name}`", "inline": True},
                        {"name": "Length", "value": str(len(name)), "inline": True},
                        {"name": "Score", "value": f"{score}/100", "inline": True},
                        {"name": "NameMC", "value": profile_url, "inline": False},
                        {"name": "Timestamp", "value": timestamp, "inline": False},
                    ],
                }
            ]
        }

    async def notify_available(self, name: str, score: int) -> bool:
        """Send a non-blocking notification for an available name.

        Any network or HTTP error is logged and suppressed so the scan never
        crashes because of a webhook problem.

        Args:
            name: The available username.
            score: Coolness score of the name.

        Returns:
            ``True`` if Discord acknowledged the webhook, ``False`` otherwise.
        """
        payload = self._build_payload(name, score)
        timeout = aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT_SECONDS)

        try:
            async with self._session.post(
                self.webhook_url, json=payload, timeout=timeout
            ) as response:
                if response.status == DISCORD_SUCCESS_STATUS:
                    return True
                # Surface the body so misconfigured webhooks are debuggable.
                body = await response.text()
                LOGGER.warning(
                    "Discord webhook returned %s for '%s': %s",
                    response.status,
                    name,
                    body[:200],
                )
                return False
        except (aiohttp.ClientError, TimeoutError) as exc:
            LOGGER.warning("Discord webhook failed for '%s': %s", name, exc)
            return False
