"""Constants for the eBlocker integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "eblocker"
MANUFACTURER: Final = "eBlocker"

DEFAULT_PORT: Final = 3000
DEFAULT_SCAN_INTERVAL: Final = 60  # seconds
MIN_SCAN_INTERVAL: Final = 15
DEFAULT_PAUSE_MINUTES: Final = 15

CONF_TRACKED_DEVICES: Final = "tracked_devices"
CONF_PAUSE_MINUTES: Final = "pause_minutes"

ATTR_DURATION: Final = "duration"
ATTR_NAME: Final = "name"
ATTR_DESCRIPTION: Final = "description"
ATTR_CATEGORY: Final = "category"
ATTR_FORMAT: Final = "format"
ATTR_URL: Final = "url"
ATTR_DOMAINS: Final = "domains"
ATTR_FILTER_TYPE: Final = "filter_type"
ATTR_UPDATE_INTERVAL: Final = "update_interval"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

SERVICE_PAUSE: Final = "pause"
SERVICE_ADD_FILTER_LIST: Final = "add_filter_list"
SERVICE_UPDATE_FILTER_LIST: Final = "update_filter_list"
SERVICE_REMOVE_FILTER_LIST: Final = "remove_filter_list"

# How long before expiry a token is renewed.
TOKEN_RENEW_MARGIN: Final = timedelta(minutes=5)
