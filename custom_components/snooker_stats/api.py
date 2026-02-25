from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import BASE_URL, HEADER_NAME

_LOGGER = logging.getLogger(__name__)

def _first_dict(payload: Any) -> dict[str, Any]:
    """snooker.org sometimes returns a list with one dict; normalize that."""
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            return payload[0]
        return {}
    if isinstance(payload, dict):
        return payload
    return {}

def _as_list(payload: Any) -> list[dict[str, Any]]:
    """Normalize payload to list[dict]."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []

class SnookerOrgApi:
    def __init__(self, hass, requested_by: str) -> None:
        self.hass = hass
        self.requested_by = requested_by

    @property
    def _headers(self) -> dict[str, str]:
        return {HEADER_NAME: self.requested_by}

    @staticmethod
    def _payload_summary(payload: Any) -> str:
        if isinstance(payload, list):
            first_keys = list(payload[0].keys()) if payload and isinstance(payload[0], dict) else []
            return f"list(len={len(payload)}, first_keys={first_keys})"
        if isinstance(payload, dict):
            return f"dict(keys={list(payload.keys())})"
        return f"{type(payload).__name__}"

    async def _get_json(self, params: dict[str, Any], endpoint: str, retry_context: str | None = None) -> Any:
        session = async_get_clientsession(self.hass)
        attempt = 0
        while True:
            attempt += 1
            start = perf_counter()
            _LOGGER.debug("API request start: endpoint=%s params=%s attempt=%s", endpoint, params, attempt)
            try:
                async with session.get(BASE_URL, params=params, headers=self._headers, timeout=30) as resp:
                    if resp.status == 403:
                        _LOGGER.warning(
                            "API rate limited (403): endpoint=%s params=%s attempt=%s context=%s; retrying in 60 seconds",
                            endpoint,
                            params,
                            attempt,
                            retry_context or "-",
                        )
                        await asyncio.sleep(60)
                        continue

                    resp.raise_for_status()
                    payload = await resp.json(content_type=None)
                    _LOGGER.debug(
                        "API request success: endpoint=%s status=%s duration_ms=%.1f attempt=%s payload=%s",
                        endpoint,
                        resp.status,
                        (perf_counter() - start) * 1000,
                        attempt,
                        self._payload_summary(payload),
                    )
                    return payload
            except Exception:
                _LOGGER.exception(
                    "API request failed: endpoint=%s params=%s duration_ms=%.1f attempt=%s",
                    endpoint,
                    params,
                    (perf_counter() - start) * 1000,
                    attempt,
                )
                raise

    # Current season (t=20)
    async def get_current_season(self) -> dict[str, Any]:
        payload = await self._get_json({"t": 20}, endpoint="get_current_season")
        return _first_dict(payload)

    # Rankings (?rt=...&s=YYYY)
    async def get_rankings(self, season: int, ranking_type: str) -> list[dict[str, Any]]:
        payload = await self._get_json(
            {"rt": ranking_type, "s": season},
            endpoint="get_rankings",
        )
        return _as_list(payload)

    # Upcoming matches (t=14, optional tr)
    async def get_upcoming_matches(self, tour: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"t": 14}
        if tour:
            params["tr"] = tour
        return await self._get_json(params, endpoint="get_upcoming_matches")

    # Events in season (t=5, with season and optional tour)
    async def get_events_in_season(self, season: int, tour: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"t": 5, "s": season}
        if tour:
            params["tr"] = tour
        return await self._get_json(params, endpoint="get_events_in_season")

    # Current/near-live matches: t=17 (&tr=...)
    async def get_current_matches(self, tour: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"t": 17}
        if tour:
            params["tr"] = tour
        payload = await self._get_json(params, endpoint="get_current_matches")
        return _as_list(payload)

    # Matches for one event (t=6&e=ID)
    async def get_matches_of_event(self, event_id: int) -> list[dict[str, Any]]:
        return await self._get_json({"t": 6, "e": event_id}, endpoint="get_matches_of_event")

    # Player details (?p=ID)
    async def get_player(self, player_id: int, progress: str | None = None) -> dict[str, Any]:
        payload = await self._get_json(
            {"p": player_id},
            endpoint="get_player",
            retry_context=f"player_id={player_id} player_progress={progress or '-'}",
        )
        return _first_dict(payload)

    async def paced_player_fetch(self, player_ids: list[int], delay_s: float = 5.0) -> dict[int, dict[str, Any]]:
        """Fetch players with a fixed delay between calls (rate-limit friendly)."""
        out: dict[int, dict[str, Any]] = {}
        total = len(player_ids)
        _LOGGER.debug("Paced player fetch started: player_count=%s delay_s=%s", total, delay_s)
        for idx, pid in enumerate(player_ids, start=1):
            out[pid] = await self.get_player(pid, progress=f"{idx}/{total}")
            _LOGGER.debug(
                "Paced player fetch progress: fetched_player_id=%s fetched_total=%s total=%s",
                pid,
                len(out),
                total,
            )
            await asyncio.sleep(delay_s)
        _LOGGER.debug("Paced player fetch completed: fetched=%s", len(out))
        return out
