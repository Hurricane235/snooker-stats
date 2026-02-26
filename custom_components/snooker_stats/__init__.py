from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import SnookerOrgApi
from .const import (
    CONF_ENABLE_CALENDAR,
    CONF_REQUESTED_BY,
    CONF_TOURS,
    DATA_CLIENT,
    DATA_COORD_RANKINGS,
    DATA_COORD_SEASON,
    DATA_COORD_UPCOMING,
    DATA_COORD_EVENTS,
    DATA_COORD_SCORES,
    DATA_PLAYER_CACHE,
    DOMAIN,
)
from .coordinator import (
    EventsInSeasonCoordinator,
    MatchScoresCoordinator,
    RankingsCoordinator,
    SeasonCoordinator,
    UpcomingMatchesCoordinator,
    load_player_cache,
    maybe_refresh_player_cache_monthly,
)

PLATFORMS = ["sensor", "calendar"]
_LOGGER = logging.getLogger(__name__)
DATA_INIT_TASK = "init_task"
DATA_PLATFORMS_LOADED = "platforms_loaded"
SERVICE_REFRESH_SEASON = "refresh_season"
SERVICE_REFRESH_RANKINGS = "refresh_rankings"
SERVICE_REFRESH_UPCOMING = "refresh_upcoming"
SERVICE_REFRESH_EVENTS = "refresh_events"
SERVICE_REFRESH_SCORES = "refresh_scores"
SERVICE_REFRESH_ALL = "refresh_all"


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration entry when options are updated."""
    _LOGGER.debug("Reloading entry_id=%s due to updated options", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_refresh_player_cache_task(hass: HomeAssistant, api: SnookerOrgApi, player_cache) -> None:
    """Run monthly cache refresh in background without surfacing task exceptions."""
    try:
        await maybe_refresh_player_cache_monthly(hass, api, player_cache)
    except Exception:
        _LOGGER.exception("Background monthly player cache refresh failed")


async def _async_refresh_all_coordinators(
    season_coord: SeasonCoordinator,
    rankings_coord: RankingsCoordinator,
    upcoming_coord: UpcomingMatchesCoordinator,
    events_coord: EventsInSeasonCoordinator,
    scores_coord: MatchScoresCoordinator,
) -> None:
    """Kick off first refreshes without blocking config entry setup."""
    try:
        await season_coord.async_refresh()
        await rankings_coord.async_refresh()
        await upcoming_coord.async_refresh()
        await events_coord.async_refresh()
        await scores_coord.async_refresh()
    except Exception:
        _LOGGER.exception("Initial coordinator refresh task failed")


async def _async_initialize_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: SnookerOrgApi,
    player_cache,
    tours: list[str],
    enable_calendar: bool,
) -> None:
    """Deferred initialization so HA startup is not blocked by slow API work."""
    try:
        if not player_cache.players:
            _LOGGER.warning(
                "Player cache is empty for entry_id=%s; delaying platform setup until initial cache download completes",
                entry.entry_id,
            )
            await maybe_refresh_player_cache_monthly(hass, api, player_cache)
        else:
            hass.async_create_task(_async_refresh_player_cache_task(hass, api, player_cache))

        season_coord = SeasonCoordinator(hass, api)
        rankings_coord = RankingsCoordinator(hass, api, season_coord, player_cache)
        upcoming_coord = UpcomingMatchesCoordinator(hass, api, tours, player_cache)
        events_coord = EventsInSeasonCoordinator(hass, api, season_coord, tours)
        scores_coord = MatchScoresCoordinator(hass, api, tours, player_cache, events_coord)

        data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if data is None:
            _LOGGER.debug("Skipping deferred initialization for entry_id=%s because entry data was removed", entry.entry_id)
            return

        data[DATA_COORD_SEASON] = season_coord
        data[DATA_COORD_RANKINGS] = rankings_coord
        data[DATA_COORD_UPCOMING] = upcoming_coord
        data[DATA_COORD_EVENTS] = events_coord
        data[DATA_COORD_SCORES] = scores_coord

        platforms = ["sensor"] + (["calendar"] if enable_calendar else [])
        _LOGGER.debug("Deferred forwarding entry_id=%s to platforms=%s", entry.entry_id, platforms)
        await hass.config_entries.async_forward_entry_setups(entry, platforms)
        data[DATA_PLATFORMS_LOADED] = True

        # Fetch initial data in background so setup never blocks on 403 backoff.
        hass.async_create_task(
            _async_refresh_all_coordinators(season_coord, rankings_coord, upcoming_coord, events_coord, scores_coord)
        )
        _LOGGER.debug("Deferred initialization complete for entry_id=%s", entry.entry_id)
    except asyncio.CancelledError:
        _LOGGER.debug("Deferred initialization cancelled for entry_id=%s", entry.entry_id)
        raise
    except Exception:
        _LOGGER.exception("Deferred initialization failed for entry_id=%s", entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    requested_by: str = entry.data[CONF_REQUESTED_BY]
    tours: list[str] = entry.options.get(CONF_TOURS, entry.data.get(CONF_TOURS, []))
    enable_calendar: bool = entry.options.get(CONF_ENABLE_CALENDAR, entry.data.get(CONF_ENABLE_CALENDAR, False))
    _LOGGER.debug(
        "Setting up entry_id=%s with tours=%s enable_calendar=%s",
        entry.entry_id,
        tours,
        enable_calendar,
    )

    api = SnookerOrgApi(hass, requested_by=requested_by)
    player_cache = await load_player_cache(hass)
    _LOGGER.debug(
        "Loaded player cache for entry_id=%s with %s players (last_refreshed=%s)",
        entry.entry_id,
        len(player_cache.players),
        player_cache.last_refreshed,
    )

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: api,
        DATA_PLAYER_CACHE: player_cache,
        DATA_COORD_SEASON: None,
        DATA_COORD_RANKINGS: None,
        DATA_COORD_UPCOMING: None,
        DATA_COORD_EVENTS: None,
        DATA_COORD_SCORES: None,
        "enable_calendar": enable_calendar,
        DATA_PLATFORMS_LOADED: False,
    }
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_SEASON):
        async def _refresh_key(data_key: str) -> int:
            refreshed = 0
            for entry_data in hass.data.get(DOMAIN, {}).values():
                coord = entry_data.get(data_key)
                if coord is None:
                    continue
                await coord.async_request_refresh()
                refreshed += 1
            return refreshed

        async def _handle_refresh_season(call) -> None:
            refreshed = await _refresh_key(DATA_COORD_SEASON)
            if refreshed == 0:
                _LOGGER.warning("Manual service refresh_season completed with no active coordinators")
            _LOGGER.info("Manual service refresh_season completed: refreshed_entries=%s", refreshed)

        async def _handle_refresh_rankings(call) -> None:
            refreshed = await _refresh_key(DATA_COORD_RANKINGS)
            if refreshed == 0:
                _LOGGER.warning("Manual service refresh_rankings completed with no active coordinators")
            _LOGGER.info("Manual service refresh_rankings completed: refreshed_entries=%s", refreshed)

        async def _handle_refresh_upcoming(call) -> None:
            refreshed = await _refresh_key(DATA_COORD_UPCOMING)
            if refreshed == 0:
                _LOGGER.warning("Manual service refresh_upcoming completed with no active coordinators")
            _LOGGER.info("Manual service refresh_upcoming completed: refreshed_entries=%s", refreshed)

        async def _handle_refresh_events(call) -> None:
            refreshed = await _refresh_key(DATA_COORD_EVENTS)
            if refreshed == 0:
                _LOGGER.warning("Manual service refresh_events completed with no active coordinators")
            _LOGGER.info("Manual service refresh_events completed: refreshed_entries=%s", refreshed)

        async def _handle_refresh_scores(call) -> None:
            refreshed = await _refresh_key(DATA_COORD_SCORES)
            if refreshed == 0:
                _LOGGER.warning("Manual service refresh_scores completed with no active coordinators")
            _LOGGER.info("Manual service refresh_scores completed: refreshed_entries=%s", refreshed)

        async def _handle_refresh_all(call) -> None:
            refreshed_season = await _refresh_key(DATA_COORD_SEASON)
            refreshed_rankings = await _refresh_key(DATA_COORD_RANKINGS)
            refreshed_upcoming = await _refresh_key(DATA_COORD_UPCOMING)
            refreshed_events = await _refresh_key(DATA_COORD_EVENTS)
            refreshed_scores = await _refresh_key(DATA_COORD_SCORES)
            if (refreshed_season + refreshed_rankings + refreshed_upcoming + refreshed_events + refreshed_scores) == 0:
                _LOGGER.warning("Manual service refresh_all completed with no active coordinators")
            _LOGGER.info(
                "Manual service refresh_all completed: season=%s rankings=%s upcoming=%s events=%s scores=%s",
                refreshed_season,
                refreshed_rankings,
                refreshed_upcoming,
                refreshed_events,
                refreshed_scores,
            )

        hass.services.async_register(DOMAIN, SERVICE_REFRESH_SEASON, _handle_refresh_season)
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_RANKINGS, _handle_refresh_rankings)
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_UPCOMING, _handle_refresh_upcoming)
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_EVENTS, _handle_refresh_events)
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_SCORES, _handle_refresh_scores)
        hass.services.async_register(DOMAIN, SERVICE_REFRESH_ALL, _handle_refresh_all)
        _LOGGER.debug("Registered services for domain=%s", DOMAIN)

    init_task = hass.async_create_task(
        _async_initialize_entry(
            hass=hass,
            entry=entry,
            api=api,
            player_cache=player_cache,
            tours=tours,
            enable_calendar=enable_calendar,
        )
    )
    hass.data[DOMAIN][entry.entry_id][DATA_INIT_TASK] = init_task
    _LOGGER.debug("Scheduled deferred initialization task for entry_id=%s", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data[DOMAIN].get(entry.entry_id)
    if not data:
        _LOGGER.debug("Unload requested for entry_id=%s but no data was present", entry.entry_id)
        return True

    init_task = data.get(DATA_INIT_TASK)
    if init_task and not init_task.done():
        init_task.cancel()
        _LOGGER.debug("Cancelled deferred initialization task for entry_id=%s", entry.entry_id)

    if not data.get(DATA_PLATFORMS_LOADED):
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            for service_name in (
                SERVICE_REFRESH_SEASON,
                SERVICE_REFRESH_RANKINGS,
                SERVICE_REFRESH_UPCOMING,
                SERVICE_REFRESH_EVENTS,
                SERVICE_REFRESH_SCORES,
                SERVICE_REFRESH_ALL,
            ):
                if hass.services.has_service(DOMAIN, service_name):
                    hass.services.async_remove(DOMAIN, service_name)
            _LOGGER.debug("Removed services for domain=%s", DOMAIN)
        _LOGGER.debug("Entry_id=%s unloaded before platforms were created", entry.entry_id)
        return True

    platforms = ["sensor"] + (["calendar"] if data.get("enable_calendar") else [])
    _LOGGER.debug("Unloading entry_id=%s from platforms=%s", entry.entry_id, platforms)
    ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            for service_name in (
                SERVICE_REFRESH_SEASON,
                SERVICE_REFRESH_RANKINGS,
                SERVICE_REFRESH_UPCOMING,
                SERVICE_REFRESH_EVENTS,
                SERVICE_REFRESH_SCORES,
                SERVICE_REFRESH_ALL,
            ):
                if hass.services.has_service(DOMAIN, service_name):
                    hass.services.async_remove(DOMAIN, service_name)
            _LOGGER.debug("Removed services for domain=%s", DOMAIN)
        _LOGGER.debug("Entry_id=%s unloaded successfully", entry.entry_id)
    else:
        _LOGGER.debug("Entry_id=%s failed to unload from platforms=%s", entry.entry_id, platforms)
    return ok
