"""DataUpdateCoordinator for the Zenbi integration.

Polls schedule + board posts only — the people-directory search is
deliberately never touched here (see api.py's async_search_people
docstring and the README for why). Each cycle re-authenticates rather than
using the refresh token Zenbi's login response includes; that refresh flow
hasn't been reverse-engineered yet, so this matches the simpler (if
slightly less efficient) re-login-per-poll behavior already proven to work.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZenbiApiError, async_fetch_boards, async_fetch_directory, async_fetch_schedule, async_login
from .const import (
    BOARDS_LOOKAHEAD_DAYS,
    BOARDS_LOOKBACK_DAYS,
    CONF_DEVICE_ID,
    DEFAULT_DIRECTORY_CACHE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SCHEDULE_FETCH_WEEKS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ZenbiData:
    """Snapshot of one successful poll."""

    lessons_by_class: dict[str, list[dict]]
    boards: list[dict]
    fetched_at: datetime


class ZenbiCoordinator(DataUpdateCoordinator[ZenbiData]):
    """Fetches Zenbi's schedule + opslagstavle feeds on a fixed interval."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)),
        )
        self.entry = entry
        self.session = async_get_clientsession(hass)

    async def _async_update_data(self) -> ZenbiData:
        username = self.entry.data["username"]
        password = self.entry.data["password"]
        device_id = self.entry.data[CONF_DEVICE_ID]

        try:
            zenbi_session = await async_login(self.session, username, password, device_id)

            now = datetime.now(timezone.utc).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            schedule_end = now + timedelta(weeks=SCHEDULE_FETCH_WEEKS)
            lessons = await async_fetch_schedule(self.session, zenbi_session.token, now, schedule_end)

            boards_start = datetime.now(timezone.utc).astimezone() - timedelta(days=BOARDS_LOOKBACK_DAYS)
            boards_end = datetime.now(timezone.utc).astimezone() + timedelta(days=BOARDS_LOOKAHEAD_DAYS)
            boards = await async_fetch_boards(self.session, zenbi_session.token, boards_start, boards_end)
        except ZenbiApiError as err:
            raise UpdateFailed(str(err)) from err

        lessons_by_class: dict[str, list[dict]] = {}
        for lesson in lessons:
            lessons_by_class.setdefault(lesson["class_code"], []).append(lesson)

        return ZenbiData(lessons_by_class=lessons_by_class, boards=boards, fetched_at=datetime.now())


@dataclass
class ZenbiDirectoryData:
    """Snapshot of one directory-cache poll: the *entire* school family
    directory, unfiltered."""

    people: list[dict]
    relations: list[dict]
    fetched_at: datetime


class ZenbiDirectoryCoordinator(DataUpdateCoordinator[ZenbiDirectoryData]):
    """Opt-in, OFF BY DEFAULT. Polls and caches the entire school family
    directory (every person + parent/child relation Zenbi's bulk globaldata
    call returns) as standing sensor state.

    This is deliberately not how the rest of this integration works — see
    api.py's async_search_people docstring and the README's "Why the
    directory search is a service, not a sensor" section for the reasoning.
    Turning live, on-demand directory access into a standing structured
    copy sitting in Home Assistant (a system that's often less locked-down
    than Zenbi itself — shared tablets, local network access, etc.)
    accumulates exposure regardless of whether anyone ever looks at the
    cached data. Only enabled if a user explicitly turns on
    CONF_ENABLE_DIRECTORY_CACHE in the integration's options; nothing about
    what Zenbi itself permits the account to see changes, but this does
    turn that same access into a standing, disk-persisted copy.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_directory",
            update_interval=timedelta(
                seconds=entry.options.get("directory_cache_interval", DEFAULT_DIRECTORY_CACHE_INTERVAL)
            ),
        )
        self.entry = entry
        self.session = async_get_clientsession(hass)

    async def _async_update_data(self) -> ZenbiDirectoryData:
        username = self.entry.data["username"]
        password = self.entry.data["password"]
        device_id = self.entry.data[CONF_DEVICE_ID]

        try:
            zenbi_session = await async_login(self.session, username, password, device_id)
            people, relations = await async_fetch_directory(self.session, zenbi_session.token)
        except ZenbiApiError as err:
            raise UpdateFailed(str(err)) from err

        return ZenbiDirectoryData(people=people, relations=relations, fetched_at=datetime.now())


@dataclass
class ZenbiRuntimeData:
    """Everything stashed in hass.data[DOMAIN][entry_id] for one config entry."""

    coordinator: ZenbiCoordinator
    directory_coordinator: ZenbiDirectoryCoordinator | None = None
