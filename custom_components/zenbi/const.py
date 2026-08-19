"""Constants for the Zenbi integration."""
import re

DOMAIN = "zenbi"

ZENBI_BASE = "https://app.zenbi.dk/api"

# Sent at login — Zenbi's backend validates this server-side as a real
# System.Guid-parseable string (a plain non-GUID string 400s), so it can't be
# an arbitrary stable value. Generated once per config entry and stored in
# entry data, kept fixed across polls in case Zenbi ties per-device
# trust/2FA state to it.
CONF_DEVICE_ID = "device_id"

CONF_CHILDREN = "children"

# Opt-in, OFF by default. Turns on ZenbiDirectoryCoordinator/
# ZenbiDirectoryCacheSensor, which polls and stores the *entire* school
# family directory as standing sensor state instead of the default
# live-lookup-only zenbi.search_people service. See sensor.py's
# ZenbiDirectoryCacheSensor docstring and the README before enabling —
# this deliberately re-adds the standing-copy exposure the service-call
# design otherwise avoids.
CONF_ENABLE_DIRECTORY_CACHE = "enable_directory_cache"
DEFAULT_DIRECTORY_CACHE_INTERVAL = 21600  # 6h — deliberately much slower than the main poll

DEFAULT_SCAN_INTERVAL = 1800  # 30 min, matches Aula-shared refresh cadence
SAMPLE_WINDOW_DAYS = 14  # config-flow class-code discovery sample window
SCHEDULE_FETCH_WEEKS = 5  # how far ahead the coordinator fetches lessons
BOARDS_LOOKBACK_DAYS = 14
BOARDS_LOOKAHEAD_DAYS = 30

# Distinguishes a real per-class lesson title prefix ("4A", "7A", "10B")
# from grade-band/staff entries sharing the same merged calendar feed
# ("Tilsyn", "7.-9.kl", "Personalemøde") — same purpose the original
# hand-written KLASSE_TO_CHILD map served, generalized so no per-family
# hardcoding is needed. Needs validation against real accounts beyond the
# one this was built against; if a school uses a differently-shaped class
# code, tune this pattern.
CLASS_CODE_RE = re.compile(r"^\d{1,2}\.?\s?[A-ZÆØÅ]{1,2}$")

# Only these relationTypes (resolved from /authentication/api/v1/relationtypes)
# represent an actual parent/guardian link — the same list shares its shape
# with non-family categories (contacts/leisurefriends/familynetwork/
# othercollaborations) and the class teacher ("Klasseleder"), which must be
# filtered out rather than shown as a guardian.
FAMILY_RELATION_TYPES = {"Far", "Mor", "Bonusfar", "Bonusmor", "Plejemor"}

# personFieldId -> our short key, resolved at runtime via
# /authentication/api/v1/globaldata/personfields (school-configurable custom
# fields, but these core ones are consistently named "form-fields.X" across
# every account tested).
DIRECTORY_FIELD_MAP = {
    "form-fields.Fullname": "name",
    "form-fields.Address": "address",
    "form-fields.ZIPcode": "zip",
    "form-fields.City": "city",
    "form-fields.MobilePhone": "phone",
}

MIN_SEARCH_QUERY_LENGTH = 2
