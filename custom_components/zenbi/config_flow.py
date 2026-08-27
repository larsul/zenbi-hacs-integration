"""Config flow for the Zenbi integration.

NOTE: written against the long-standing OptionsFlow pattern (config_entry
passed into __init__). HA has been migrating this across recent releases
(newer cores provide self.config_entry as a base-class property and warn on
manually assigning it) — this hasn't been validated against a live HA core
checkout (none available locally, see the repo's verification notes), so
double-check this against whatever HA version you're actually running
before shipping a release.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZenbiApiError, ZenbiAuthError, ZenbiSession, async_fetch_schedule, async_login
from .const import CONF_CHILDREN, CONF_DEVICE_ID, CONF_ENABLE_DIRECTORY_CACHE, DOMAIN, SAMPLE_WINDOW_DAYS

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)


async def _discover_class_codes(session, token: str) -> list[str]:
    """Sample the schedule feed to find which class codes actually show up,
    so the children-naming step only asks about codes that are real for this
    account instead of requiring the user to know/guess them up front."""
    now = datetime.now(timezone.utc).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + timedelta(days=SAMPLE_WINDOW_DAYS)
    lessons = await async_fetch_schedule(session, token, now, end)
    return sorted({lesson["class_code"] for lesson in lessons})


class ZenbiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Zenbi config flow: credentials, then optional child naming."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._password: str | None = None
        self._device_id: str | None = None
        self._class_codes: list[str] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            device_id = str(uuid.uuid4())
            try:
                zenbi_session: ZenbiSession = await async_login(
                    session, user_input["username"], user_input["password"], device_id
                )
            except ZenbiAuthError:
                errors["base"] = "invalid_auth"
            except ZenbiApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input["username"].lower())
                self._abort_if_unique_id_configured()

                self._username = user_input["username"]
                self._password = user_input["password"]
                self._device_id = device_id

                try:
                    self._class_codes = await _discover_class_codes(session, zenbi_session.token)
                except ZenbiApiError:
                    # Sample fetch failing shouldn't block setup — the options
                    # flow can retry class-code discovery later.
                    self._class_codes = []

                if not self._class_codes:
                    return self._create_entry({})
                return await self.async_step_children()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_children(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            children = {code: name for code, name in user_input.items() if name}
            return self._create_entry(children)

        schema = vol.Schema({vol.Optional(code): str for code in self._class_codes})
        return self.async_show_form(
            step_id="children",
            data_schema=schema,
            description_placeholders={"codes": ", ".join(self._class_codes)},
        )

    def _create_entry(self, children: dict[str, str]):
        return self.async_create_entry(
            title=self._username or "Zenbi",
            data={
                "username": self._username,
                "password": self._password,
                CONF_DEVICE_ID: self._device_id,
            },
            options={CONF_CHILDREN: children},
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> config_entries.ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None and self._reauth_entry is not None:
            session = async_get_clientsession(self.hass)
            device_id = self._reauth_entry.data.get(CONF_DEVICE_ID) or str(uuid.uuid4())
            try:
                await async_login(session, user_input["username"], user_input["password"], device_id)
            except ZenbiAuthError:
                errors["base"] = "invalid_auth"
            except ZenbiApiError:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        "username": user_input["username"],
                        "password": user_input["password"],
                        CONF_DEVICE_ID: device_id,
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(step_id="reauth_confirm", data_schema=STEP_USER_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> ZenbiOptionsFlow:
        return ZenbiOptionsFlow(config_entry)


class ZenbiOptionsFlow(config_entries.OptionsFlow):
    """Re-run child-name discovery/editing any time (new school year, a
    class code that wasn't in the original sample window, etc.), followed
    by the (off-by-default, opt-in) directory-cache toggle.

    NOTE: stores the entry as `self._entry`, deliberately NOT `self.config_entry`
    - recent HA core (confirmed against a 2026-era dev build) turned
    `OptionsFlow.config_entry` into a property with no setter, so the
    long-standing `self.config_entry = config_entry` pattern this class used
    to follow now raises `AttributeError: property 'config_entry' of
    'ZenbiOptionsFlow' object has no setter` the moment the options flow is
    opened. Using a differently-named attribute sidesteps the property
    entirely rather than depending on exactly which HA version's inherited
    `config_entry` behavior is active.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._class_codes: list[str] = []
        self._children: dict[str, str] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        session = async_get_clientsession(self.hass)
        try:
            zenbi_session = await async_login(
                session,
                self._entry.data["username"],
                self._entry.data["password"],
                self._entry.data[CONF_DEVICE_ID],
            )
            self._class_codes = await _discover_class_codes(session, zenbi_session.token)
        except ZenbiApiError:
            # Fall back to whatever codes are already configured rather than
            # locking the user out of the options flow on a transient error.
            self._class_codes = list(self._entry.options.get(CONF_CHILDREN, {}).keys())

        return await self.async_step_children()

    async def async_step_children(self, user_input: dict[str, Any] | None = None):
        current = self._entry.options.get(CONF_CHILDREN, {})
        if user_input is not None:
            self._children = {code: name for code, name in user_input.items() if name}
            return await self.async_step_directory_cache()

        schema = vol.Schema({vol.Optional(code, default=current.get(code, "")): str for code in self._class_codes})
        return self.async_show_form(step_id="children", data_schema=schema)

    async def async_step_directory_cache(self, user_input: dict[str, Any] | None = None):
        """Separate, explicit step for the opt-in directory-cache sensor —
        kept out of the children step on purpose so this isn't something a
        user can toggle on by accident while renaming a class. Off by
        default; see ZenbiDirectoryCacheSensor's docstring and the README's
        "Why the directory search is a service, not a sensor" section
        before turning it on."""
        current_enabled = self._entry.options.get(CONF_ENABLE_DIRECTORY_CACHE, False)
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_CHILDREN: self._children,
                    CONF_ENABLE_DIRECTORY_CACHE: user_input[CONF_ENABLE_DIRECTORY_CACHE],
                },
            )

        schema = vol.Schema({vol.Required(CONF_ENABLE_DIRECTORY_CACHE, default=current_enabled): bool})
        return self.async_show_form(step_id="directory_cache", data_schema=schema)
