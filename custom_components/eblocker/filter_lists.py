"""Helpers for eBlocker filter lists ("blockers")."""

from __future__ import annotations

from typing import Any

from homeassistant.util import slugify

# eBlocker category of a filter list -> value used in Home Assistant
CATEGORIES = {
    "ads": "ADS",
    "trackers": "TRACKER",
    "malware": "MALWARE",
    "parental_control": "PARENTAL_CONTROL",
}

# List format -> blocker type (domain lists vs. URL pattern lists)
FORMATS = {
    "domains": ("DOMAINS", "DOMAIN"),
    "etc_hosts": ("ETC_HOSTS", "DOMAIN"),
    "squid_acl": ("SQUID_ACL", "DOMAIN"),
    "easylist": ("EASYLIST", "PATTERN"),
    "urls": ("URLS", "PATTERN"),
}

FILTER_TYPES = ("blacklist", "whitelist")
UPDATE_INTERVALS = {"daily": "DAILY", "never": "NEVER"}


def is_custom(blocker: dict[str, Any]) -> bool:
    """Return True for user-created filter lists."""
    return not blocker.get("providedByEblocker", True)


def blocker_key(blocker: dict[str, Any]) -> str:
    """Return a key that stays stable across eBlocker restarts.

    Custom lists have persistent IDs. Built-in lists only get temporary IDs
    (assigned in access order), so they are identified by type, category and
    name instead.
    """
    if is_custom(blocker):
        return f"custom_{blocker['id']}"
    names = blocker.get("name") or {}
    name = names.get("en") or next(iter(names.values()), str(blocker.get("id")))
    return slugify(f"builtin_{blocker.get('type')}_{blocker.get('category')}_{name}")


def blocker_name(blocker: dict[str, Any], language: str) -> str:
    """Return the name of a filter list in the given language."""
    names = blocker.get("name") or {}
    lang = (language or "en").split("-")[0]
    return (
        names.get(lang)
        or names.get("en")
        or next(iter(names.values()), f"#{blocker.get('id')}")
    )


def build_blocker(
    *,
    name: str,
    category: str,
    list_format: str,
    url: str | None,
    domains: list[str] | None,
    filter_type: str,
    update_interval: str | None,
    description: str | None,
    language: str,
) -> dict[str, Any]:
    """Build the payload for creating a custom filter list."""
    eb_format, blocker_type = FORMATS[list_format]
    lang = (language or "en").split("-")[0]
    payload: dict[str, Any] = {
        "name": {lang: name},
        "description": {lang: description or name},
        "type": blocker_type,
        "category": CATEGORIES[category],
        "format": eb_format,
        "filterType": filter_type,
        "providedByEblocker": False,
        "enabled": True,
    }
    if url:
        payload["url"] = url
        payload["updateInterval"] = UPDATE_INTERVALS[update_interval or "daily"]
    else:
        payload["content"] = "\n".join(d.strip() for d in domains or [] if d.strip())
        payload["updateInterval"] = UPDATE_INTERVALS[update_interval or "never"]
    return payload
