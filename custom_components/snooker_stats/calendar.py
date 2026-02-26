from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DATA_COORD_EVENTS, DATA_COORD_UPCOMING, DOMAIN, TOUR_LABELS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    upcoming_coord = data[DATA_COORD_UPCOMING]
    events_coord = data[DATA_COORD_EVENTS]
    entities = [SnookerUpcomingCalendar(upcoming_coord, events_coord, tour_code=tour_code) for tour_code in upcoming_coord.tours]
    async_add_entities(entities, True)


class SnookerUpcomingCalendar(CalendarEntity):
    def __init__(self, coord, events_coord, tour_code: str):
        self.coordinator = coord
        self.events_coordinator = events_coord
        self.tour_code = tour_code
        self._attr_name = f"Snooker Upcoming Matches ({TOUR_LABELS.get(tour_code, tour_code)})"
        self._attr_unique_id = f"snooker_org_calendar_upcoming_{tour_code}"
        self._unsub_coordinator = None
        self._unsub_events_coordinator = None

    async def async_added_to_hass(self):
        self._unsub_coordinator = self.coordinator.async_add_listener(self.async_write_ha_state)
        self._unsub_events_coordinator = self.events_coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_coordinator:
            self._unsub_coordinator()
            self._unsub_coordinator = None
        if self._unsub_events_coordinator:
            self._unsub_events_coordinator()
            self._unsub_events_coordinator = None

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.utcnow()
        upcoming = self._collect_events(start_date=now, end_date=now + timedelta(days=365))
        return upcoming[0] if upcoming else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime):
        return self._collect_events(start_date=start_date, end_date=end_date)

    def _collect_events(self, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        start_utc = dt_util.as_utc(start_date) if start_date.tzinfo else dt_util.as_utc(dt_util.as_local(start_date))
        end_utc = dt_util.as_utc(end_date) if end_date.tzinfo else dt_util.as_utc(dt_util.as_local(end_date))

        data = self.coordinator.data or {}
        matches: list[dict[str, Any]] = data.get("matches", [])
        events: list[CalendarEvent] = []

        for m in matches:
            if m.get("Tour") != self.tour_code:
                continue

            raw = m.get("ScheduledDate")
            if not raw:
                continue

            # Many snooker.org payloads use "YYYY-MM-DD HH:MM:SS" style strings; parse loosely
            dt = dt_util.parse_datetime(str(raw).replace(" ", "T"))
            if not dt:
                continue

            dt = dt_util.as_utc(dt) if dt.tzinfo else dt_util.as_utc(dt_util.as_local(dt))

            if dt < start_utc or dt > end_utc:
                continue

            p1 = self._resolve_player_name(m.get("Player1ID"))
            p2 = self._resolve_player_name(m.get("Player2ID"))
            event_name = f"{p1} vs {p2}"

            # Assume 2-hour default duration if not provided
            end_dt = dt + timedelta(hours=2)

            events.append(
                CalendarEvent(
                    summary=event_name,
                    start=dt,
                    end=end_dt,
                    description=self._event_details(m.get("EventID")),
                    location="",
                )
            )
        events.sort(key=lambda e: e.start)
        return events

    def _resolve_player_name(self, player_id: Any) -> str:
        try:
            pid = int(player_id)
        except Exception:
            return "TBD"
        return self.coordinator.player_cache.players.get(pid, f"#{pid}")

    def _event_details(self, event_id: Any) -> str:
        try:
            eid = int(event_id)
        except Exception:
            return "Unknown - Unknown - Unknown - Unknown"
        event_map = (self.events_coordinator.data or {}).get("events_by_id", {})
        info = event_map.get(eid, {})
        name = str(info.get("Name") or "Unknown")
        event_type = str(info.get("Type") or "Unknown")
        city = str(info.get("City") or "Unknown")
        venue = str(info.get("Venue") or "Unknown")
        return f"{name} - {event_type} - {city} - {venue}"
