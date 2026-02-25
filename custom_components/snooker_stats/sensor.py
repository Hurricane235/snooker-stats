from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_COORD_EVENTS, DATA_COORD_RANKINGS, DATA_COORD_SCORES, DATA_COORD_SEASON, DATA_COORD_UPCOMING, DOMAIN

UPCOMING_MATCH_ATTR_LIMIT = 25
EVENT_ATTR_LIMIT = 50


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            CurrentSeasonSensor(data[DATA_COORD_SEASON]),
            Top10MoneySensor(data[DATA_COORD_RANKINGS]),
            Top10OneYearMoneySensor(data[DATA_COORD_RANKINGS]),
            UpcomingMatchesSensor(data[DATA_COORD_UPCOMING]),
            EventsInSeasonSensor(data[DATA_COORD_EVENTS]),
            CurrentMatchScoresSensor(data[DATA_COORD_SCORES]),
        ],
        True,
    )


class CurrentSeasonSensor(SensorEntity):
    _attr_name = "Snooker Current Season"
    _attr_unique_id = "snooker_org_current_season"

    def __init__(self, coord):
        self.coordinator = coord

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        return d.get("Season") or d.get("ID")

    @property
    def extra_state_attributes(self):
        return self.coordinator.data or {}


class Top10MoneySensor(SensorEntity):
    _attr_name = "Snooker Top 10 (Money Rankings)"
    _attr_unique_id = "snooker_org_top10_money"

    def __init__(self, coord):
        self.coordinator = coord

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        return len(d.get("top10_money", []))

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        return {"season": d.get("season"), "top10": d.get("top10_money", [])}


class Top10OneYearMoneySensor(SensorEntity):
    _attr_name = "Snooker Top 10 (One-Year Money Rankings)"
    _attr_unique_id = "snooker_org_top10_one_year_money"

    def __init__(self, coord):
        self.coordinator = coord

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        return len(d.get("top10_one_year_money", []))

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        return {"season": d.get("season"), "top10": d.get("top10_one_year_money", [])}


class UpcomingMatchesSensor(SensorEntity):
    _attr_name = "Snooker Upcoming Matches"
    _attr_unique_id = "snooker_org_upcoming_matches"

    def __init__(self, coord):
        self.coordinator = coord

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        return d.get("count", 0)

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        matches = d.get("matches", [])
        limited_matches = matches[:UPCOMING_MATCH_ATTR_LIMIT]
        return {
            "matches": limited_matches,
            "matches_total": len(matches),
            "matches_truncated": len(matches) > UPCOMING_MATCH_ATTR_LIMIT,
        }


class EventsInSeasonSensor(SensorEntity):
    _attr_name = "Snooker Events In Season"
    _attr_unique_id = "snooker_org_events_in_season"

    def __init__(self, coord):
        self.coordinator = coord

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        return d.get("count", 0)

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        events = d.get("events", [])
        limited_events = events[:EVENT_ATTR_LIMIT]
        return {
            "season": d.get("season"),
            "events": limited_events,
            "events_total": len(events),
            "events_truncated": len(events) > EVENT_ATTR_LIMIT,
        }


class CurrentMatchScoresSensor(SensorEntity):
    _attr_name = "Snooker Current Match Scores"
    _attr_unique_id = "snooker_org_current_match_scores"

    def __init__(self, coord):
        self.coordinator = coord

    async def async_added_to_hass(self):
        self.coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def native_value(self):
        d = self.coordinator.data or {}
        return d.get("count", 0)

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        matches = d.get("matches", [])
        limited_matches = matches[:UPCOMING_MATCH_ATTR_LIMIT]
        return {
            "matches": limited_matches,
            "matches_total": len(matches),
            "matches_truncated": len(matches) > UPCOMING_MATCH_ATTR_LIMIT,
        }
