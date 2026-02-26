from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    RANKING_MONEY,
    RANKING_ONE_YEAR_MONEY,
)

_LOGGER = logging.getLogger(__name__)

PLAYER_CACHE_STORE_VERSION = 1
PLAYER_CACHE_STORE_KEY = f"{DOMAIN}_player_cache"

MONTHLY_REFRESH_DAYS = 30


@dataclass
class PlayerCache:
    # maps player_id -> display_name
    players: dict[int, str]
    last_refreshed: str | None  # ISO string


async def load_player_cache(hass: HomeAssistant) -> PlayerCache:
    store = Store(hass, PLAYER_CACHE_STORE_VERSION, PLAYER_CACHE_STORE_KEY)
    data = await store.async_load() or {}
    players_raw = data.get("players", {})
    players = {int(k): str(v) for k, v in players_raw.items()}
    _LOGGER.debug(
        "Loaded player cache from storage: players=%s last_refreshed=%s",
        len(players),
        data.get("last_refreshed"),
    )
    return PlayerCache(players=players, last_refreshed=data.get("last_refreshed"))


async def save_player_cache(hass: HomeAssistant, cache: PlayerCache) -> None:
    store = Store(hass, PLAYER_CACHE_STORE_VERSION, PLAYER_CACHE_STORE_KEY)
    await store.async_save(
        {"players": {str(k): v for k, v in cache.players.items()}, "last_refreshed": cache.last_refreshed}
    )
    _LOGGER.debug(
        "Saved player cache to storage: players=%s last_refreshed=%s",
        len(cache.players),
        cache.last_refreshed,
    )


def _player_name_from_payload(p: dict[str, Any]) -> str:
    # The player payload fields vary; this tries common patterns safely.
    for key in ("Name", "FullName", "DisplayName"):
        if key in p and p[key]:
            return str(p[key])
    # fallback to concatenation if FirstName/LastName exist
    fn = str(p.get("FirstName", "")).strip()
    ln = str(p.get("LastName", "")).strip()
    name = (fn + " " + ln).strip()
    return name or f"Player {p.get('ID', '?')}"


def _extract_season(payload: dict[str, Any]) -> int:
    """Extract season robustly from varying current-season payload fields."""
    for key in ("Season", "ID", "CurrentSeason", "SeasonID"):
        raw = payload.get(key)
        if raw in (None, ""):
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            _LOGGER.debug("Could not parse season field %s=%s (%s)", key, raw, type(raw).__name__)
            continue
    raise ValueError(f"No usable season value in payload keys={list(payload.keys())}")


class SeasonCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, api) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="snooker_org_current_season",
            update_interval=timedelta(days=1),
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            _LOGGER.debug("SeasonCoordinator refresh started")
            payload = await self.api.get_current_season()
            _LOGGER.debug(
                "SeasonCoordinator response: keys=%s season=%s id=%s",
                list(payload.keys()),
                payload.get("Season"),
                payload.get("ID"),
            )
            return payload
        except Exception as err:
            raise UpdateFailed(f"Failed to fetch current season: {err}") from err


class RankingsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, api, season_coord: SeasonCoordinator, player_cache: PlayerCache) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="snooker_org_rankings",
            update_interval=timedelta(days=7),
        )
        self.api = api
        self.season_coord = season_coord
        self.player_cache = player_cache

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            _LOGGER.debug("RankingsCoordinator refresh started")
            season_payload = self.season_coord.data or await self.api.get_current_season()
            season = _extract_season(season_payload)
            _LOGGER.debug("RankingsCoordinator using season=%s season_payload_keys=%s", season, list(season_payload.keys()))
            money = await self.api.get_rankings(season, RANKING_MONEY)
            one_year = await self.api.get_rankings(season, RANKING_ONE_YEAR_MONEY)
            _LOGGER.debug(
                "RankingsCoordinator API response sizes: money=%s one_year=%s",
                len(money),
                len(one_year),
            )

            # Top 10, with names resolved via cache if possible
            def decorate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
                out = []
                for r in rows[:10]:
                    pid = int(r.get("PlayerID", r.get("ID", 0)) or 0)
                    name = self.player_cache.players.get(pid)
                    out.append({**r, "PlayerName": name or f"#{pid}"})
                return out

            result = {
                "season": season,
                "top10_money": decorate(money),
                "top10_one_year_money": decorate(one_year),
            }
            _LOGGER.debug(
                "RankingsCoordinator decorated result: top10_money=%s top10_one_year=%s first_money_keys=%s",
                len(result["top10_money"]),
                len(result["top10_one_year_money"]),
                list((result["top10_money"][0] if result["top10_money"] else {}).keys()),
            )
            return result
        except Exception as err:
            raise UpdateFailed(f"Failed to fetch rankings: {err}") from err


class UpcomingMatchesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, api, tours: list[str], player_cache: PlayerCache) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="snooker_org_upcoming",
            update_interval=timedelta(days=1),
        )
        self.api = api
        self.tours = tours
        self.player_cache = player_cache

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            # pull upcoming for each tour and merge
            _LOGGER.debug("UpcomingMatchesCoordinator refresh started: tours=%s", self.tours)
            all_matches: list[dict[str, Any]] = []
            for tr in self.tours:
                tour_matches = await self.api.get_upcoming_matches(tr)
                _LOGGER.debug("UpcomingMatchesCoordinator API response for tour=%s: count=%s", tr, len(tour_matches))
                all_matches.extend([{**match, "Tour": tr} for match in tour_matches])

            def as_int(pid: Any) -> int | None:
                try:
                    return int(pid)
                except Exception:
                    return None

            decorated: list[dict[str, Any]] = []
            for m in all_matches:
                p1 = m.get("Player1ID") or m.get("P1") or m.get("Player1")
                p2 = m.get("Player2ID") or m.get("P2") or m.get("Player2")
                scheduled = m.get("ScheduledDate") or m.get("StartDate") or m.get("Date")
                if not scheduled:
                    continue

                decorated.append(
                    {
                        "Tour": str(m.get("Tour") or ""),
                        "EventID": as_int(m.get("EventID") or m.get("Event") or m.get("EID")),
                        "ScheduledDate": str(scheduled),
                        "Player1ID": as_int(p1),
                        "Player2ID": as_int(p2),
                    }
                )

            missing_player_ids: set[int] = set()
            for match in decorated:
                for key in ("Player1ID", "Player2ID"):
                    pid = match.get(key)
                    if pid is None:
                        continue
                    if pid not in self.player_cache.players:
                        missing_player_ids.add(pid)

            if missing_player_ids:
                _LOGGER.debug(
                    "UpcomingMatchesCoordinator found missing players in cache: count=%s",
                    len(missing_player_ids),
                )
                try:
                    players_payload = await self.api.paced_player_fetch(sorted(missing_player_ids), delay_s=1.0)
                    added = 0
                    for pid, payload in players_payload.items():
                        self.player_cache.players[pid] = _player_name_from_payload(payload)
                        added += 1
                    await save_player_cache(self.hass, self.player_cache)
                    _LOGGER.debug(
                        "UpcomingMatchesCoordinator updated player cache from matches: added_or_updated=%s total_cached=%s",
                        added,
                        len(self.player_cache.players),
                    )
                except Exception:
                    _LOGGER.exception("UpcomingMatchesCoordinator failed to enrich missing players from API")

            # Sort by scheduled date string
            def sort_key(x: dict[str, Any]) -> str:
                return str(x.get("ScheduledDate") or "")

            decorated.sort(key=sort_key)
            result = {"count": len(decorated), "matches": decorated}
            _LOGGER.debug(
                "UpcomingMatchesCoordinator merged result: total=%s first_match_keys=%s",
                result["count"],
                list((result["matches"][0] if result["matches"] else {}).keys()),
            )
            return result
        except Exception as err:
            raise UpdateFailed(f"Failed to fetch upcoming matches: {err}") from err


class EventsInSeasonCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, api, season_coord: SeasonCoordinator, tours: list[str]) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="snooker_org_events_in_season",
            update_interval=timedelta(days=1),
        )
        self.api = api
        self.season_coord = season_coord
        self.tours = tours

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            _LOGGER.debug("EventsInSeasonCoordinator refresh started: tours=%s", self.tours)
            season_payload = self.season_coord.data or await self.api.get_current_season()
            season = _extract_season(season_payload)

            raw_events: list[dict[str, Any]] = []
            if self.tours:
                for tr in self.tours:
                    tour_events = await self.api.get_events_in_season(season, tr)
                    _LOGGER.debug("EventsInSeasonCoordinator API response for tour=%s: count=%s", tr, len(tour_events))
                    raw_events.extend(tour_events)
            else:
                raw_events = await self.api.get_events_in_season(season)
                _LOGGER.debug("EventsInSeasonCoordinator API response (no tour filter): count=%s", len(raw_events))

            events_by_id: dict[int, dict[str, Any]] = {}
            for raw in raw_events:
                event_id = raw.get("ID") or raw.get("EventID") or raw.get("EID")
                if event_id is None:
                    continue
                try:
                    event_id_int = int(event_id)
                except (TypeError, ValueError):
                    continue

                event = {
                    "ID": event_id_int,
                    "Name": str(raw.get("Name") or raw.get("EventName") or ""),
                    "City": str(raw.get("City") or ""),
                    "Venue": str(raw.get("Venue") or ""),
                    "Type": str(raw.get("Type") or ""),
                    "StartDate": str(raw.get("StartDate") or raw.get("Start") or ""),
                    "EndDate": str(raw.get("EndDate") or raw.get("End") or ""),
                }
                events_by_id[event_id_int] = event
                _LOGGER.debug(
                    "Event metadata: ID=%s Name=%s City=%s Venue=%s Type=%s",
                    event["ID"],
                    event["Name"],
                    event["City"],
                    event["Venue"],
                    event["Type"],
                )

            events = sorted(events_by_id.values(), key=lambda x: x["ID"])
            _LOGGER.debug("EventsInSeasonCoordinator normalized events: season=%s count=%s", season, len(events))
            return {
                "season": season,
                "count": len(events),
                "events": events,
                "events_by_id": events_by_id,
            }
        except Exception as err:
            raise UpdateFailed(f"Failed to fetch events in season: {err}") from err


class MatchScoresCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        api,
        tours: list[str],
        player_cache: PlayerCache,
        events_coord: EventsInSeasonCoordinator,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="snooker_org_match_scores",
            update_interval=timedelta(minutes=5),
        )
        self.api = api
        self.tours = tours
        self.player_cache = player_cache
        self.events_coord = events_coord

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            _LOGGER.debug("MatchScoresCoordinator refresh started: tours=%s", self.tours)
            raw_matches: list[dict[str, Any]] = []
            for tr in self.tours:
                tour_matches = await self.api.get_current_matches(tr)
                _LOGGER.debug("MatchScoresCoordinator API response for tour=%s: count=%s", tr, len(tour_matches))
                raw_matches.extend(tour_matches)

            events_map = (self.events_coord.data or {}).get("events_by_id", {})
            matches: list[dict[str, Any]] = []
            missing_player_ids: set[int] = set()

            def as_int(val: Any) -> int | None:
                if val in (None, ""):
                    return None
                try:
                    return int(val)
                except (TypeError, ValueError):
                    return None

            for m in raw_matches:
                p1_id = as_int(m.get("Player1ID"))
                p2_id = as_int(m.get("Player2ID"))
                event_id = as_int(m.get("EventID"))

                if p1_id and p1_id not in self.player_cache.players:
                    missing_player_ids.add(p1_id)
                if p2_id and p2_id not in self.player_cache.players:
                    missing_player_ids.add(p2_id)

                event = events_map.get(event_id or -1, {})
                matches.append(
                    {
                        "MatchID": int(m.get("ID") or 0),
                        "EventID": event_id,
                        "EventName": event.get("Name") or "",
                        "EventType": event.get("Type") or "",
                        "EventCity": event.get("City") or "",
                        "Player1ID": p1_id,
                        "Player1Name": self.player_cache.players.get(p1_id or -1, f"#{p1_id}" if p1_id else "TBD"),
                        "Score1": int(m.get("Score1") or 0),
                        "Player2ID": p2_id,
                        "Player2Name": self.player_cache.players.get(p2_id or -1, f"#{p2_id}" if p2_id else "TBD"),
                        "Score2": int(m.get("Score2") or 0),
                        "Status": int(m.get("Status") or 0),
                        "Unfinished": bool(m.get("Unfinished")),
                        "ScheduledDate": str(m.get("ScheduledDate") or ""),
                        "StartDate": str(m.get("StartDate") or ""),
                        "EndDate": str(m.get("EndDate") or ""),
                    }
                )

            if missing_player_ids:
                _LOGGER.debug("MatchScoresCoordinator missing players to enrich: count=%s", len(missing_player_ids))
                try:
                    players_payload = await self.api.paced_player_fetch(sorted(missing_player_ids), delay_s=1.0)
                    updated = 0
                    for pid, payload in players_payload.items():
                        self.player_cache.players[pid] = _player_name_from_payload(payload)
                        updated += 1
                    await save_player_cache(self.hass, self.player_cache)
                    _LOGGER.debug("MatchScoresCoordinator enriched players: updated=%s", updated)

                    for match in matches:
                        p1_id = match.get("Player1ID")
                        p2_id = match.get("Player2ID")
                        if p1_id:
                            match["Player1Name"] = self.player_cache.players.get(p1_id, match["Player1Name"])
                        if p2_id:
                            match["Player2Name"] = self.player_cache.players.get(p2_id, match["Player2Name"])
                except Exception:
                    _LOGGER.exception("MatchScoresCoordinator failed to enrich missing players")

            matches.sort(key=lambda x: (x.get("ScheduledDate") or "", x.get("MatchID") or 0))
            result = {"count": len(matches), "matches": matches}
            _LOGGER.debug(
                "MatchScoresCoordinator normalized result: count=%s first_match_keys=%s",
                result["count"],
                list((result["matches"][0] if result["matches"] else {}).keys()),
            )
            return result
        except Exception as err:
            raise UpdateFailed(f"Failed to fetch current match scores: {err}") from err


async def maybe_refresh_player_cache_monthly(hass: HomeAssistant, api, cache: PlayerCache) -> None:
    """Run at startup and then monthly (guarded by last_refreshed)."""
    now = dt_util.utcnow()
    _LOGGER.debug(
        "Monthly player cache refresh check started: players=%s last_refreshed=%s now=%s",
        len(cache.players),
        cache.last_refreshed,
        now.isoformat(),
    )
    if cache.last_refreshed:
        try:
            last = dt_util.parse_datetime(cache.last_refreshed)
        except Exception:
            last = None
        if last and (now - last) < timedelta(days=MONTHLY_REFRESH_DAYS):
            _LOGGER.debug(
                "Skipping monthly cache refresh: age_days=%.2f threshold_days=%s",
                (now - last).total_seconds() / 86400,
                MONTHLY_REFRESH_DAYS,
            )
            return

    # Determine current season, then rankings -> top 100 IDs
    season_payload = await api.get_current_season()
    season = _extract_season(season_payload)
    _LOGGER.debug("Monthly cache refresh season payload keys=%s resolved_season=%s", list(season_payload.keys()), season)

    rankings = await api.get_rankings(season, RANKING_MONEY)
    _LOGGER.debug("Monthly cache refresh rankings fetched: count=%s", len(rankings))
    top100_ids: list[int] = []
    for r in rankings[:100]:
        pid = r.get("PlayerID", r.get("ID"))
        if pid is None:
            continue
        top100_ids.append(int(pid))
    _LOGGER.debug("Monthly cache refresh top100 player ids prepared: count=%s", len(top100_ids))

    # Paced fetch (5s delay per player as requested)
    _LOGGER.debug("Monthly cache refresh paced player fetch started: ids=%s delay_s=5.0", len(top100_ids))
    players_payload = await api.paced_player_fetch(top100_ids, delay_s=5.0)
    _LOGGER.debug("Monthly cache refresh paced player fetch completed: payload_count=%s", len(players_payload))

    # Update cache
    updated = 0
    for pid, payload in players_payload.items():
        cache.players[pid] = _player_name_from_payload(payload)
        updated += 1

    cache.last_refreshed = now.isoformat()
    await save_player_cache(hass, cache)
    _LOGGER.debug(
        "Monthly cache refresh complete: updated=%s total_cached=%s last_refreshed=%s",
        updated,
        len(cache.players),
        cache.last_refreshed,
    )
