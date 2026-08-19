"""Async client for Zenbi's private, undocumented API.

There is no public Zenbi API — every endpoint here was reverse-engineered
against the real app.zenbi.dk web app (see docs/zenbi-api-scrape.md for the
full writeup). This may break without notice if Zenbi changes their app.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import (
    CLASS_CODE_RE,
    DIRECTORY_FIELD_MAP,
    FAMILY_RELATION_TYPES,
    ZENBI_BASE,
)

_LOGGER = logging.getLogger(__name__)


class ZenbiApiError(Exception):
    """Raised on any non-auth Zenbi API failure."""


class ZenbiAuthError(ZenbiApiError):
    """Raised when login fails — wrong credentials or a locked account."""


@dataclass
class ZenbiSession:
    """The bits of the login response later calls need."""

    token: str
    refresh_token: str | None
    user_id: str | None
    raw: dict[str, Any] = field(default_factory=dict)


def _iso(dt: datetime) -> str:
    return quote(dt.isoformat())


def _delta_to_text(description: Any) -> str:
    """Quill Delta, sent as a JSON *string* (not a parsed object) -> plain text."""
    if not description:
        return ""
    try:
        delta = json.loads(description) if isinstance(description, str) else description
    except json.JSONDecodeError:
        return ""
    if not isinstance(delta, dict) or "ops" not in delta:
        return ""
    parts = [op["insert"] for op in delta["ops"] if isinstance(op.get("insert"), str)]
    return "".join(parts).strip()


def _split_class(title: str | None) -> tuple[str | None, str | None]:
    """Lesson titles are "<klasse> <fag>" (e.g. "4A matematik") -> ("4A", "matematik")."""
    if not title or " " not in title:
        return None, title
    klasse, _, rest = title.partition(" ")
    return klasse, rest


async def async_login(
    session: aiohttp.ClientSession, username: str, password: str, device_id: str
) -> ZenbiSession:
    """Authenticate and return the session token/refresh token/own user id."""
    try:
        async with session.post(
            f"{ZENBI_BASE}/authentication/api/v1/account/authenticate",
            json={"username": username, "password": password, "uniqueDeviceId": device_id},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (400, 401, 403):
                raise ZenbiAuthError(f"Zenbi login rejected (HTTP {resp.status})")
            resp.raise_for_status()
            data = await resp.json()
    except aiohttp.ClientError as err:
        raise ZenbiApiError(f"Zenbi login request failed: {err}") from err

    token = data.get("token")
    if not token:
        raise ZenbiAuthError("Zenbi login response had no token")
    return ZenbiSession(token=token, refresh_token=data.get("refreshToken"), user_id=data.get("userId"), raw=data)


async def _get_json(
    session: aiohttp.ClientSession, token: str, path: str, params: dict[str, str] | None = None
) -> Any:
    try:
        async with session.get(
            f"{ZENBI_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
    except aiohttp.ClientError as err:
        raise ZenbiApiError(f"Zenbi request failed ({path}): {err}") from err


async def async_fetch_homework_by_calendar_item(
    session: aiohttp.ClientSession, token: str, start: datetime, end: datetime
) -> dict[str, str]:
    """Per-lesson homework is NOT on the calendar item itself (description/note
    are always empty there) — it's a separate feed keyed by calendarItemId."""
    items = await _get_json(
        session, token, f"/calendar/api/v1/homeworks/relations?start={_iso(start)}&end={_iso(end)}"
    )
    return {
        item["calendarItemId"]: _delta_to_text(item.get("description"))
        for item in items
        if item.get("calendarItemId")
    }


async def async_fetch_schedule(
    session: aiohttp.ClientSession, token: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Both/all of the account's own children's lessons, merged into one feed
    and told apart only by the class-code prefix on each title — see
    CLASS_CODE_RE in const.py. Returns normalized lesson dicts tagged with
    `class_code`; callers group by that code themselves."""
    items = await _get_json(
        session, token, f"/calendar/api/v1/calendaritems/own/relation/users?start={_iso(start)}&end={_iso(end)}"
    )
    homework_by_item = await async_fetch_homework_by_calendar_item(session, token, start, end)

    lessons: list[dict[str, Any]] = []
    for item in items:
        klasse, subject = _split_class(item.get("title"))
        if not klasse or not CLASS_CODE_RE.match(klasse):
            continue  # staff/grade-band entry (e.g. "Tilsyn", "7.-9.kl"), not a per-class lesson
        lessons.append(
            {
                "class_code": klasse,
                "title": subject,
                "start": item.get("start"),
                "end": item.get("end"),
                "allDay": bool(item.get("onlyDate")),
                "substitute": bool(item.get("substitutes")),
                "homework": homework_by_item.get(item.get("id")) or None,
            }
        )
    return lessons


async def async_fetch_boards(
    session: aiohttp.ClientSession, token: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """School-wide opslagstavle/board posts, shared across all children."""
    data = await _get_json(
        session,
        token,
        f"/message/api/v1/boards/date?start={_iso(start)}&end={_iso(end)}&skip=0&take=40",
    )
    boards = data.get("boards", []) if isinstance(data, dict) else data
    return [
        {
            "title": board.get("title"),
            "text": _delta_to_text(board.get("description")),
            "date": board.get("start") or board.get("createdTime"),
        }
        for board in boards
    ]


async def async_fetch_directory(
    session: aiohttp.ClientSession, token: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch and parse Zenbi's bulk globaldata call into the full school
    family directory: every person it returns, plus resolved parent/child
    relations. This is the same underlying call Zenbi's own /home/users/
    "Personer" page makes (it loads this once and filters client-side).

    Shared by two very different callers with very different intents:
    async_search_people (filters this down to a live query, returns only
    matches, keeps nothing) and the opt-in ZenbiDirectoryCacheSensor
    (stores the unfiltered result as standing sensor state). The fetch
    itself always pulls the *whole* directory either way — see those two
    callers' docstrings for why what happens to the result matters so much.
    """
    globaldata = await _get_json(session, token, "/authentication/api/v1/globaldata")
    personfields = await _get_json(session, token, "/authentication/api/v1/globaldata/personfields")
    relationtypes = await _get_json(session, token, "/authentication/api/v1/relationtypes")

    field_names = {
        f["id"]: f.get("name")
        for f in personfields.get("defaultPersonFields", []) + personfields.get("userDefinedPersonFields", [])
    }
    relation_type_names = {rt["id"]: rt.get("name") for rt in relationtypes.get("relationTypes", [])}

    models: list[dict[str, Any]] = []
    for tf in globaldata.get("userModelTimeFrames", []):
        models.extend(tf.get("userModels", []))

    persons: dict[str, dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []
    seen_relations: set[tuple[str | None, str | None, str | None]] = set()

    for m in models:
        uid = m.get("id")
        if not uid:
            continue
        fields: dict[str, Any] = {}
        for md in m.get("userMetadata", []):
            key = DIRECTORY_FIELD_MAP.get(field_names.get(md.get("personFieldId")))
            if key:
                fields[key] = md.get("value")
        if uid not in persons:
            persons[uid] = {
                "id": uid,
                "name": fields.get("name"),
                "address": fields.get("address"),
                "zip": fields.get("zip"),
                "city": fields.get("city"),
                "phone": fields.get("phone"),
            }
        for rel in m.get("userRelations", []):
            rtype = relation_type_names.get(rel.get("relationTypeId"))
            if rtype not in FAMILY_RELATION_TYPES:
                continue
            key = (rel.get("parentUserId"), rel.get("childUserId"), rtype)
            if key in seen_relations:
                continue
            seen_relations.add(key)
            relations.append({"parentId": rel.get("parentUserId"), "childId": rel.get("childUserId"), "relationType": rtype})

    return list(persons.values()), relations


async def async_search_people(session: aiohttp.ClientSession, token: str, query: str) -> dict[str, Any]:
    """On-demand school-directory lookup, backing the search feature on Zenbi's
    own /home/users/ "Personer" page. Nothing here is cached or persisted
    between calls, by design (see the integration's README for why: this
    pulls other families' contact info, and the whole point of doing it as
    a live service call instead of a polled sensor is to avoid quietly
    accumulating a standing copy of the school directory).
    """
    persons, relations = await async_fetch_directory(session, token)

    q = query.strip().lower()
    matched_ids: set[str] = {p["id"] for p in persons if p["name"] and q in p["name"].lower()}

    # Include anyone directly matched, plus the immediate parent/child
    # counterparts of a match, so a search for a kid's name also surfaces
    # their parent's address and vice versa — same as the dashboard's
    # existing join logic.
    linked_ids: set[str] = set()
    kept_relations = []
    for rel in relations:
        if rel["parentId"] in matched_ids or rel["childId"] in matched_ids:
            kept_relations.append(rel)
            linked_ids.add(rel["parentId"])
            linked_ids.add(rel["childId"])

    result_ids = matched_ids | linked_ids
    people = [p for p in persons if p["id"] in result_ids]

    return {"people": people, "relations": kept_relations}
