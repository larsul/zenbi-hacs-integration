"""Sensor platform for the Zenbi integration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CHILDREN, DOMAIN
from .coordinator import ZenbiCoordinator, ZenbiDirectoryCoordinator, ZenbiRuntimeData


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    runtime: ZenbiRuntimeData = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator

    entities: list[SensorEntity] = [ZenbiBoardsSensor(coordinator, entry), ZenbiStatusSensor(coordinator, entry)]

    # One schedule sensor per class code actually seen in the first
    # successful poll — codes discovered later (e.g. a new school year) only
    # show up after a reload, same as any other coordinator-driven dynamic
    # entity set; re-running the options flow triggers that reload.
    for class_code in sorted(coordinator.data.lessons_by_class):
        entities.append(ZenbiScheduleSensor(coordinator, entry, class_code))

    # Opt-in, off by default — this entity only exists at all if the user
    # explicitly turned on the directory cache in the integration's options
    # (see ZenbiDirectoryCacheSensor's docstring and the README).
    if runtime.directory_coordinator is not None:
        entities.append(ZenbiDirectoryCacheSensor(runtime.directory_coordinator, entry))

    async_add_entities(entities)


class ZenbiScheduleSensor(CoordinatorEntity[ZenbiCoordinator], SensorEntity):
    """Upcoming lessons for one child/class, identified by class code."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ZenbiCoordinator, entry: ConfigEntry, class_code: str) -> None:
        super().__init__(coordinator)
        self._class_code = class_code
        friendly = entry.options.get(CONF_CHILDREN, {}).get(class_code)
        self._attr_name = friendly or f"Klasse {class_code}"
        self._attr_unique_id = f"{entry.entry_id}_{class_code}_schedule"
        self._attr_icon = "mdi:calendar-week"

    @property
    def _lessons(self) -> list[dict]:
        return self.coordinator.data.lessons_by_class.get(self._class_code, [])

    @property
    def native_value(self) -> int:
        now = datetime.now(timezone.utc).astimezone()
        week_end = now + timedelta(days=7)
        count = 0
        for lesson in self._lessons:
            start = lesson.get("start")
            if not start:
                continue
            try:
                start_dt = datetime.fromisoformat(start)
            except ValueError:
                continue
            if now <= start_dt <= week_end:
                count += 1
        return count

    @property
    def extra_state_attributes(self) -> dict:
        return {"class_code": self._class_code, "lessons": self._lessons}


class ZenbiBoardsSensor(CoordinatorEntity[ZenbiCoordinator], SensorEntity):
    """School-wide opslagstavle/board posts."""

    _attr_has_entity_name = True
    _attr_name = "Opslagstavle"
    _attr_icon = "mdi:bulletin-board"

    def __init__(self, coordinator: ZenbiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_opslagstavle"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.boards)

    @property
    def extra_state_attributes(self) -> dict:
        return {"boards": self.coordinator.data.boards}


class ZenbiStatusSensor(CoordinatorEntity[ZenbiCoordinator], SensorEntity):
    """Last-successful-fetch time, so a dashboard can flag stale data instead
    of going blank on a failed poll (the coordinator keeps serving the last
    good data on failure — this just makes that visible)."""

    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_icon = "mdi:information-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ZenbiCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self) -> str:
        return "ok" if self.coordinator.last_update_success else "error"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "fetched_at": self.coordinator.data.fetched_at.isoformat() if self.coordinator.data else None,
            "last_exception": str(self.coordinator.last_exception) if self.coordinator.last_exception else None,
        }


class ZenbiDirectoryCacheSensor(CoordinatorEntity[ZenbiDirectoryCoordinator], SensorEntity):
    """OFF BY DEFAULT — only created when CONF_ENABLE_DIRECTORY_CACHE is
    turned on in the integration's options.

    A standing, structured copy of the *entire* school family directory
    (name/address/phone/parent-child links for every family Zenbi's bulk
    endpoint returns), refreshed on a timer and kept as sensor state,
    instead of the default zenbi.search_people service's live/on-demand,
    nothing-retained behavior. Read the README's "Why the directory search
    is a service, not a sensor" section before enabling this — it
    knowingly re-adds the standing-copy exposure that design avoids. Only
    turn it on if you specifically want a cached directory and accept that
    tradeoff for your own Home Assistant instance."""

    _attr_has_entity_name = True
    _attr_name = "Directory cache"
    _attr_icon = "mdi:account-group"

    def __init__(self, coordinator: ZenbiDirectoryCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_directory_cache"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.people)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "people": self.coordinator.data.people,
            "relations": self.coordinator.data.relations,
            "fetched_at": self.coordinator.data.fetched_at.isoformat(),
        }
