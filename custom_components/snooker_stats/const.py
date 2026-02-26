from __future__ import annotations

DOMAIN = "snooker_stats"

CONF_REQUESTED_BY = "requested_by"
CONF_TOURS = "tours"
CONF_ENABLE_CALENDAR = "enable_calendar"

DEFAULT_ENABLE_CALENDAR = False

# Internal keys
DATA_CLIENT = "client"
DATA_COORD_SEASON = "coord_season"
DATA_COORD_RANKINGS = "coord_rankings"
DATA_COORD_UPCOMING = "coord_upcoming"
DATA_COORD_EVENTS = "coord_events"
DATA_COORD_SCORES = "coord_scores"
DATA_PLAYER_CACHE = "player_cache"

# API
BASE_URL = "https://api.snooker.org/"
HEADER_NAME = "X-Requested-By"  # required by snooker.org API docs

# These are the human-facing UI labels and their API codes
TOUR_CHOICES = {
    "Main tour": "main",
    "Q Tour": "q",
    "Seniors": "seniors",
    "Women": "women",
    "EBSA": "ebsa",
    "WSF": "wsf",
    "Other": "other",
}
TOUR_LABELS = {code: label for label, code in TOUR_CHOICES.items()}

# Ranking types (from API docs)
RANKING_MONEY = "MoneyRankings"
RANKING_ONE_YEAR_MONEY = "OneYearMoneyRankings"
