"""Discord webhook pings for available names.

One embed per free name. If the webhook is flaky we log it and move on - a
broken webhook should never take down a scan that's been running for hours.
"""

import logging
from datetime import datetime, timezone

import aiohttp

log = logging.getLogger(__name__)

EMBED_COLOR_SUCCESS = 0x00FF88  # green
EMBED_TITLE = "🎯 Available Minecraft Name Found!"
NAMEMC_PROFILE_URL = "https://namemc.com/profile/{name}"

WEBHOOK_TIMEOUT_SECONDS = 10.0
DISCORD_SUCCESS_STATUS = 204  # Discord returns 204 on a delivered webhook


class DiscordNotifier:
    """Posts an embed to a Discord webhook.

    Borrows the caller's aiohttp session instead of opening its own, so it
    plays nicely with the scanner's connection pool.
    """

    def __init__(self, webhook_url: str, session: aiohttp.ClientSession):
        self.webhook_url = webhook_url
        self._session = session

    def _build_payload(self, name: str, score: int) -> dict:
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
        """Fire the webhook. Errors are logged and swallowed, never raised."""
        payload = self._build_payload(name, score)
        timeout = aiohttp.ClientTimeout(total=WEBHOOK_TIMEOUT_SECONDS)

        try:
            async with self._session.post(
                self.webhook_url, json=payload, timeout=timeout
            ) as response:
                if response.status == DISCORD_SUCCESS_STATUS:
                    return True
                # Log the body so a misconfigured webhook is actually debuggable.
                body = await response.text()
                log.warning(
                    "Discord webhook returned %s for '%s': %s",
                    response.status,
                    name,
                    body[:200],
                )
                return False
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning("Discord webhook failed for '%s': %s", name, exc)
            return False
