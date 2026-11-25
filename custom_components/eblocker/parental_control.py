"""Helpers for eBlocker parental control (profiles, users, usage)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

WEEKDAYS = (
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
)

# InternetAccessContingent.onDay: 1..7 = Monday..Sunday, 8 = weekdays, 9 = weekend
CONTINGENT_DAYS = {day.lower(): index + 1 for index, day in enumerate(WEEKDAYS)}
CONTINGENT_DAYS.update({"weekdays": 8, "weekend": 9})
CONTINGENT_DAY_NAMES = {value: key for key, value in CONTINGENT_DAYS.items()}

USER_ROLES = {"child": "CHILD", "parent": "PARENT", "other": "OTHER"}

# Names of the built-in profiles as shown by the eBlocker settings UI
BUILTIN_PROFILE_NAMES = {
    "PARENTAL_CONTROL_DEFAULT_PROFILE_NAME": {
        "de": "Standard-Profil",
        "en": "Standard profile",
    },
    "PARENTAL_CONTROL_FULL_PROFILE_NAME": {
        "de": "Kinder-Profil",
        "en": "Children profile",
    },
    "PARENTAL_CONTROL_FULL_2_PROFILE_NAME": {
        "de": "Kinder-Profil mit Nutzungsdauer",
        "en": "Children profile with usage time",
    },
    "PARENTAL_CONTROL_MED_PROFILE_NAME": {
        "de": "Jugendliche mit eigenem Gerät",
        "en": "Teenagers with own device",
    },
    "PARENTAL_CONTROL_MED_2_PROFILE_NAME": {
        "de": "Jugendliche-Profil mit Nutzungsdauer",
        "en": "Teenager profile with usage time",
    },
    "PARENTAL_CONTROL_FRAG_FINN_PROFILE_NAME": {
        "de": "fragFINN-Schutzprofil",
        "en": "fragFINN protection profile",
    },
}

# Template the eBlocker UI copies into the profile of a new child (by age)
AGE_PRESETS = (
    (6, "PARENTAL_CONTROL_FRAG_FINN_PROFILE_NAME"),
    (12, "PARENTAL_CONTROL_FULL_2_PROFILE_NAME"),
    (15, "PARENTAL_CONTROL_MED_2_PROFILE_NAME"),
    (18, "PARENTAL_CONTROL_MED_PROFILE_NAME"),
)

# Fields of a profile that hold parental control settings (copied from presets)
PROFILE_SETTINGS = (
    "controlmodeUrls",
    "controlmodeTime",
    "controlmodeMaxUsage",
    "internetAccessRestrictionMode",
    "accessibleSitesPackages",
    "inaccessibleSitesPackages",
    "internetAccessContingents",
    "maxUsageTimeByDay",
)


def user_profile_owner(
    profile: dict[str, Any], users: dict[int, dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the (non-system) user a personal profile belongs to."""
    owners = [
        u
        for u in users.values()
        if not u.get("system") and u.get("associatedProfileId") == profile.get("id")
    ]
    return owners[0] if len(owners) == 1 and not profile.get("builtin") else None


def managed_profile_ids(
    profiles: dict[int, dict[str, Any]], users: dict[int, dict[str, Any]]
) -> list[int]:
    """Profiles that get entities: the standard profile and profiles in use."""
    used = {u.get("associatedProfileId") for u in users.values()}
    return sorted(
        pid
        for pid, profile in profiles.items()
        if not profile.get("hidden") and (pid in used or profile.get("standard"))
    )


def profile_name(
    profile: dict[str, Any], users: dict[int, dict[str, Any]], language: str
) -> str:
    """Return a human readable name for a profile."""
    if owner := user_profile_owner(profile, users):
        return owner.get("name") or f"#{owner.get('id')}"
    lang = (language or "en").split("-")[0]
    if names := BUILTIN_PROFILE_NAMES.get(profile.get("nameKey") or ""):
        return names.get(lang, names["en"])
    return profile.get("name") or f"#{profile.get('id')}"


def parental_list_name(filter_list: dict[str, Any], language: str) -> str:
    """Return the name of a parental control domain list."""
    lang = (language or "en").split("-")[0]
    names = filter_list.get("name") or {}
    return (
        names.get(lang)
        or names.get("en")
        or filter_list.get("customerCreatedName")
        or next(iter(names.values()), f"#{filter_list.get('id')}")
    )


PROFILE_LIST_ATTRIBUTE = {
    # filterType of a parental control list -> profile field it is stored in
    "blacklist": "inaccessibleSitesPackages",
    "whitelist": "accessibleSitesPackages",
}
RESTRICTION_MODES = {"block_listed": 1, "allow_listed_only": 2}


def selectable_parental_lists(
    lists: dict[int, dict[str, Any]] | None,
) -> dict[int, dict[str, Any]]:
    """Lists a profile can block or allow (same choice as the eBlocker UI)."""
    return {
        list_id: filter_list
        for list_id, filter_list in (lists or {}).items()
        if filter_list.get("category") == "PARENTAL_CONTROL"
        and filter_list.get("filterType") in PROFILE_LIST_ATTRIBUTE
    }


def toggle_profile_list(
    profile: dict[str, Any], filter_list: dict[str, Any], enabled: bool
) -> dict[str, Any]:
    """Add/remove a list to/from a profile like the eBlocker UI does.

    Selecting a list switches the web filter on; when no list is selected
    any more, the web filter is switched off.
    """
    attribute = PROFILE_LIST_ATTRIBUTE[filter_list["filterType"]]
    ids = [i for i in profile.get(attribute) or [] if i != filter_list["id"]]
    if enabled:
        ids.append(filter_list["id"])
    profile[attribute] = ids
    if enabled:
        profile["controlmodeUrls"] = True
    elif not profile.get("inaccessibleSitesPackages") and not profile.get(
        "accessibleSitesPackages"
    ):
        profile["controlmodeUrls"] = False
    return profile


def find_parental_lists(
    lists: dict[int, dict[str, Any]], wanted: list[str]
) -> tuple[list[int], list[str]]:
    """Resolve list names (any language, case-insensitive) or IDs to IDs."""
    ids: list[int] = []
    missing: list[str] = []
    for item in wanted:
        needle = str(item).strip().casefold()
        match = None
        for list_id, filter_list in lists.items():
            names = {str(list_id)}
            names.update(
                str(n).casefold() for n in (filter_list.get("name") or {}).values()
            )
            if filter_list.get("customerCreatedName"):
                names.add(str(filter_list["customerCreatedName"]).casefold())
            if needle in names:
                match = list_id
                break
        if match is None:
            missing.append(str(item))
        elif match not in ids:
            ids.append(match)
    return ids, missing


def preset_name_key(age: int | None) -> str:
    """Return the built-in profile the eBlocker UI uses for a child of ``age``."""
    if age is None:
        return "PARENTAL_CONTROL_DEFAULT_PROFILE_NAME"
    for limit, key in AGE_PRESETS:
        if age < limit:
            return key
    return "PARENTAL_CONTROL_DEFAULT_PROFILE_NAME"


def age_on(birthday: date, today: date) -> int:
    """Return the age in years."""
    years = today.year - birthday.year
    if (today.month, today.day) < (birthday.month, birthday.day):
        years -= 1
    return years


def parse_duration(value: Any) -> timedelta | None:
    """Parse a Java Duration as serialized by Jackson (seconds or ISO 8601)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return timedelta(seconds=float(value))
    try:
        return timedelta(seconds=float(value))
    except (TypeError, ValueError):
        return dt_util.parse_duration(str(value))


def _parse_local_datetime(value: Any) -> datetime | None:
    """Parse a Java LocalDateTime (array [y, m, d, h, min, s, ns] or ISO)."""
    if isinstance(value, list) and len(value) >= 3:
        parts = [int(p) for p in value[:6]] + [0] * (6 - min(len(value), 6))
        return datetime(*parts[:6])
    if isinstance(value, str):
        return dt_util.parse_datetime(value)
    return None


def bonus_minutes_today(profile: dict[str, Any], today: date) -> int:
    """Return today's bonus minutes of a profile."""
    bonus = profile.get("bonusTimeUsage") or {}
    when = _parse_local_datetime(bonus.get("dateTime"))
    if when is None or when.date() != today:
        return 0
    return int(bonus.get("bonusMinutes") or 0)


def usage_minutes(usage: dict[str, Any] | None) -> dict[str, float | None]:
    """Return used/maximum/remaining minutes of a usage account."""
    if not usage:
        return {"used": None, "max": None, "remaining": None}
    used = parse_duration(usage.get("accountedTime")) or parse_duration(
        usage.get("usedTime")
    )
    maximum = parse_duration(usage.get("maxUsageTime"))
    used_min = round(used.total_seconds() / 60, 1) if used is not None else None
    max_min = round(maximum.total_seconds() / 60, 1) if maximum is not None else None
    remaining = (
        max(0.0, round(max_min - used_min, 1))
        if used_min is not None and max_min is not None
        else None
    )
    return {"used": used_min, "max": max_min, "remaining": remaining}


def format_contingents(contingents: list[dict[str, Any]] | None) -> list[str]:
    """Return online time windows as readable strings ("weekdays 07:00-20:00")."""
    result = []
    for c in contingents or []:
        day = CONTINGENT_DAY_NAMES.get(c.get("onDay"), str(c.get("onDay")))
        start, end = c.get("fromMinutes") or 0, c.get("tillMinutes") or 0
        result.append(
            f"{day} {start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"
        )
    return result


def build_contingent(day: str, start: time, end: time) -> dict[str, int]:
    """Build an InternetAccessContingent."""
    from_minutes = start.hour * 60 + start.minute
    till_minutes = end.hour * 60 + end.minute
    if till_minutes == 0 and end == time(0, 0):
        till_minutes = 24 * 60  # "until midnight"
    return {
        "onDay": CONTINGENT_DAYS[day],
        "fromMinutes": from_minutes,
        "tillMinutes": till_minutes,
        "totalMinutes": till_minutes - from_minutes,
    }
