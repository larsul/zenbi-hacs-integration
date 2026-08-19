# Zenbi API — full endpoint scrape

This integration only needs a handful of endpoints (login, schedule, homework,
opslagstavle, people search). This doc is the result of pointing the same
reverse-engineering method at the *whole* web app — `app.zenbi.dk` is a large
multi-tenant school-admin platform, not just a parent portal, so there's a lot
more API surface than this integration uses.

> ⚠️ Unofficial/reverse-engineered, same status as the rest of this repo. Nothing
> here changes that — this just maps what else exists, for reference.

## Method

1. Fetched `https://app.zenbi.dk/` and its top-level `/assets/*.js` chunks.
2. Grepped those for every referenced chunk filename (Vite's `__vite__mapDeps`
   arrays list every lazy-loaded route chunk by name) — 777 chunks total, i.e.
   effectively the entire app, not just the handful of calendar/message code
   paths this integration actually calls.
3. Downloaded all 777 (~12 MB of public static JS, same as any browser loads
   visiting the site) and regex-matched every `.../api/v{n}/...` path string
   across all of them → **746 unique endpoint paths**.
4. Grouped by the module segment right after `/api/` (`calendar`, `homework`,
   `message`, …) — see the table below.
5. For the modules plausibly relevant to a parent account, found each list
   endpoint's actual call site (`serializeParams`) to get the real query-param
   names, then hit a handful live with our own login to confirm they're
   reachable from a parent token (not staff-only) and see what they return today.

This is the same method used to find the endpoints this integration actually
uses (see `custom_components/zenbi/api.py`) — just run across the whole app
instead of just the handful of endpoints needed here.

## Domain map (746 endpoints, by module)

| Module | Endpoints | Parent-relevant? |
|---|---:|---|
| `authentication` | 177 | Partly — login itself; rest is account/org admin |
| `planning` | 90 | No — staff scheduling/worktime |
| `calendar` | 64 | **Yes** — schedule, homework-in-calendar, checkinout |
| `economy` | 46 | No — invoicing/billing admin |
| `signup` | 41 | Maybe — event signups, untested |
| `questionnaire` | 37 | **Yes** — permission slips etc., see below |
| `message` | 33 | **Yes** — opslagstavle boards + a separate DM inbox |
| `grade` | 31 | No — all under `administration/`, teacher-side grading setup |
| `medicine` | 31 | No — staff medicine-administration logging |
| `externalmessage` | 27 | No — e-conomic bookkeeping integration (school's accounting, not ours) |
| `homework` | 23 | **Yes** — already used (`homeworks/relations`) |
| `infoscreen` | 21 | No — hallway TV/signage config |
| `notification` | 21 | **Yes** — general notification feed, see below |
| `diary` | 20 | **Yes** — SFO/daycare diary, see below |
| `planningautoscheduling` | 20 | No — staff auto-scheduling admin |
| `organisation` | 18 | No — org/licensing admin |
| `absence` | 12 | No — all under `system/`/`planning/`, absence-reporting *config*, not a per-kid "report sick" call |
| `evaluation` | 11 | Untested |
| `actionplan` | 10 | No — staff pedagogical action plans |
| `injury` | 7 | No — staff injury-report logging |
| `payment` | 4 | Untested |
| `checkinout` | 1 | Maybe — `planning/checkinouts`, distinct from `calendar`'s own checkinout endpoint |
| `signalr` | 1 | Realtime push (SignalR hub) — not needed for polling |

## Endpoints already used by this integration

- `POST /authentication/api/v1/account/authenticate` — login
- `GET /calendar/api/v1/calendaritems/own/relation/users?start=&end=` — schedule
- `GET /calendar/api/v1/homeworks/relations?start=&end=` — homework notes, keyed by `calendarItemId`
- `GET /message/api/v1/boards/date?start=&end=&skip=&take=` — opslagstavle
- `GET /authentication/api/v1/globaldata`, `.../globaldata/personfields`, `.../relationtypes` — backs `zenbi.search_people` (mirrors Zenbi's own "Personer" directory search)

## Additional endpoints confirmed reachable from a parent login (untapped)

All of these returned **HTTP 200** from our real Zenbi account (not 401/403 —
genuinely available to a parent, not staff-only), tested 2026-08-10. All came back
empty for the tested account's school right now — either the school doesn't use that
feature, or there's nothing in the tested window — so shapes below are the *schema*,
not real content:

| Endpoint | Params | Response shape (live, empty) |
|---|---|---|
| `GET /message/api/v1/messageitems` | none required | `{messageParents, messageChilds, tapbacks, messageComments, messageReceivers, messageTags, individualAdaptations}` — a **direct-message inbox**, separate from the opslagstavle boards |
| `GET /message/api/v1/messageitems/unread` | none required | same shape, unread-only |
| `GET /notification/api/v1/notifications` | none required | `{notifications, unReadInternallyIds, unReadEventIds}` |
| `GET /notification/api/v1/notifications/unread` | none required | same shape, unread-only |
| `GET /calendar/api/v1/checkinout/calendaritems/own?start=&end=` | `start`, `end` (same encoding as the schedule calls) | `[]` — SFO/afterschool check-in/out times, if the school tracks them |
| `GET /diary/api/v1/diary/relations?starttime=&endtime=` | **`starttime`/`endtime`**, not `start`/`end` | `{diaries, readTimes, tapbacks, comments}` — SFO/daycare diary entries |
| `GET /questionnaire/api/v1/home/questionnaires/relations?fromDate=&endDate=` | **`fromDate`/`endDate`**, not `start`/`end` | `{questionnaires, intervals, answers, answerItems, ...}` — permission slips / forms sent to parents |
| `GET /homework/api/v1/weeklyschedules/relations?start=&end=` | `start`, `end` | `[]` — a separate "weekly plan" concept from individual lessons |

**Gotcha worth flagging if any of these get built out later:** every module
reinvents its own date-param names — `calendar`/`homework` use `start`/`end`,
`diary` uses `starttime`/`endtime`, `questionnaire` uses `fromDate`/`endDate`. Don't
assume `start`/`end` works everywhere; check the module's own call site first
(same grep-the-bundle method as above).

`GET /notification/api/v1/notification/summary` was also probed but needs an `id`
query param (400 without one) — likely a per-relation (per-kid) notification
summary; not pursued further since we don't have a documented way to enumerate
relation IDs by name yet.

## Not pursued

Everything else in the 746 — `planning`, `economy`, `externalmessage`, `grade`
(admin-side), `medicine`, `infoscreen`, `organisation`, `absence` (admin/system
config), `actionplan`, `injury`, `planningautoscheduling`, `evaluation`,
`payment`, `signup` — is staff/admin tooling for running the school (scheduling,
billing, grading setup, medical logs, signage) that a parent account has no
obvious use for, even where it might technically be reachable. Not tested live;
flagged here only so a future search starts from the domain list instead of from
zero.
