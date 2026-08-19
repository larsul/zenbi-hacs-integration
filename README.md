# Zenbi for Home Assistant

A [HACS](https://hacs.xyz/) custom integration for [Zenbi](https://app.zenbi.dk) — the school-home platform used by many Danish schools — exposing your children's schedule, homework, school opslagstavle (bulletin board) posts, and an on-demand school-directory search as Home Assistant sensors and a service.

> **⚠️ Unofficial / reverse-engineered.** Zenbi has no public API. Every endpoint this integration uses was found by reading Zenbi's own web app (see [`docs/zenbi-api-scrape.md`](docs/zenbi-api-scrape.md) for the full writeup). It can break without notice if Zenbi changes their app, and it isn't affiliated with or endorsed by Zenbi. Use at your own risk, and don't rely on it for anything time-critical.

## What it does

- **Per-child schedule sensor** — one sensor per class code found in your account, with upcoming lessons (including homework notes) as an attribute.
- **Opslagstavle sensor** — school-wide board posts.
- **Status sensor** — last successful fetch time, so a dashboard can flag stale data instead of going blank on a failed poll.
- **`zenbi.search_people` service** — search the school directory by name and get back a matched student/parent plus their linked family relations (address, city, phone, where the family has filled those in). This is a live, on-demand lookup, **not** a cached sensor — see [Why the directory search is a service, not a sensor](#why-the-directory-search-is-a-service-not-a-sensor) below.
- **Directory cache sensor** *(optional, off by default)* — a standing sensor with the whole school directory as state, for anyone who explicitly wants that instead of the live service call. See [Optional: caching the directory anyway](#optional-caching-the-directory-anyway-advanced-off-by-default) below before turning it on.

## Install

1. In HACS, add this repository as a **custom repository** (category: Integration): `https://github.com/larsul/zenbi-hacs-integration`.
2. Install "Zenbi", restart Home Assistant.
3. Settings → Devices & Services → Add Integration → Zenbi. Enter the same username/password you use at app.zenbi.dk.
4. If your account has children in more than one class, you'll be asked to optionally name each class code that was found (e.g. "4A" → "Emma"). You can skip this and use the raw class codes, or change it later from the integration's **Configure** button.

## Entities

| Entity | What it is |
|---|---|
| `sensor.<name>` (one per class code) | Lesson count in the next 7 days; `lessons` attribute has the full list (title, start, end, all-day, substitute flag, homework note) |
| `sensor.<account>_opslagstavle` | Board-post count; `boards` attribute has title/text/date per post |
| `sensor.<account>_status` (diagnostic) | `ok`/`error`; `fetched_at` and `last_exception` attributes |
| `sensor.<account>_directory_cache` *(optional, off by default)* | Person count; `people`, `relations`, and `fetched_at` attributes. Only exists if enabled — see below |

## Service: `zenbi.search_people`

```yaml
service: zenbi.search_people
data:
  config_entry_id: <your Zenbi config entry id>
  query: "Freja"
```

Returns (via `response_data`, so you can use this directly from a script, automation, or dashboard card that supports service responses):

```json
{
  "people": [
    {"id": "...", "name": "Freja ...", "address": null, "zip": null, "city": null, "phone": null},
    {"id": "...", "name": "Mette ...", "address": "Eksempelvej 12", "zip": "8000", "city": "Aarhus C", "phone": "12345678"}
  ],
  "relations": [
    {"parentId": "...", "childId": "...", "relationType": "Mor"}
  ]
}
```

### Why the directory search is a service, not a sensor

Zenbi's own app has a "Personer" search feature that lets any parent look up a classmate and see their parent's contact info — that's an intentional feature of the platform, gated by each parent's own login. This integration exposes the exact same thing, using your own credentials, so it doesn't grant access to anything your account couldn't already see through Zenbi's own UI.

What it deliberately does **not** do by default is turn that into a sensor that polls and caches the entire school directory on a schedule. A polled sensor would sit there quietly re-fetching and storing hundreds of other families' names, addresses, and phone numbers in your Home Assistant instance whether you ever look them up or not — a standing export that outlives any single lookup, in a system that's often less locked-down than the platform the data came from (shared tablets, local network access, etc.). A service call queries live, per search, and returns only matches — same access, same data, no silent accumulation.

### Optional: caching the directory anyway (advanced, off by default)

Some setups genuinely want a standing local copy — e.g. building a dashboard card that lists classmates without calling a service each time. If that's you, there's an explicit, separate opt-in for it: turn on **Enable directory cache sensor** from the integration's **Configure → directory cache** options step. This adds `sensor.<account>_directory_cache`, which polls and stores the *entire* school directory (not filtered to any search) as sensor state on its own (much slower, 6h-default) schedule.

This is **off by default on purpose**, and enabling it re-introduces exactly the standing-copy exposure described above — you're trading that away for convenience. Only turn it on if you've read and accept that tradeoff. If you just want to look someone up occasionally, use the `zenbi.search_people` service above instead; it's the fully-supported path and keeps nothing.

## Credits

Reverse-engineering groundwork in `docs/` originally done against a different family's Zenbi account and adapted/extended here; see those docs for the full endpoint-discovery methodology.

## License

MIT — see [LICENSE](LICENSE).
