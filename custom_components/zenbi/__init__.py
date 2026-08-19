"""The Zenbi integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZenbiApiError, ZenbiAuthError, async_login, async_search_people
from .const import CONF_DEVICE_ID, CONF_ENABLE_DIRECTORY_CACHE, DOMAIN, MIN_SEARCH_QUERY_LENGTH
from .coordinator import ZenbiCoordinator, ZenbiDirectoryCoordinator, ZenbiRuntimeData

PLATFORMS = ["sensor"]

SERVICE_SEARCH_PEOPLE = "search_people"
SEARCH_PEOPLE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("query"): vol.All(cv.string, vol.Length(min=MIN_SEARCH_QUERY_LENGTH)),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = ZenbiCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # Opt-in, off by default — see ZenbiDirectoryCoordinator's docstring and
    # the README before turning this on in the integration's options.
    directory_coordinator: ZenbiDirectoryCoordinator | None = None
    if entry.options.get(CONF_ENABLE_DIRECTORY_CACHE, False):
        directory_coordinator = ZenbiDirectoryCoordinator(hass, entry)
        await directory_coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ZenbiRuntimeData(coordinator, directory_coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SEARCH_PEOPLE)
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SEARCH_PEOPLE):
        return

    async def _handle_search_people(call: ServiceCall) -> ServiceResponse:
        """Live, on-demand school-directory lookup — deliberately not backed
        by any cached entity/sensor. See api.py's async_search_people
        docstring and the README for why this is a service, not a sensor."""
        entry_id = call.data["config_entry_id"]
        runtime: ZenbiRuntimeData | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if runtime is None:
            raise HomeAssistantError(f"Unknown Zenbi config entry: {entry_id}")

        session = async_get_clientsession(hass)
        entry = runtime.coordinator.entry
        try:
            zenbi_session = await async_login(
                session, entry.data["username"], entry.data["password"], entry.data[CONF_DEVICE_ID]
            )
            return await async_search_people(session, zenbi_session.token, call.data["query"])
        except ZenbiAuthError as err:
            raise HomeAssistantError(f"Zenbi login failed: {err}") from err
        except ZenbiApiError as err:
            raise HomeAssistantError(f"Zenbi search failed: {err}") from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_PEOPLE,
        _handle_search_people,
        schema=SEARCH_PEOPLE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
