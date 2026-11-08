"""Open data: public services that need no account and no key.

A Crew asks for a look-up in its checkpoint (`lookups`, like `weather_requests`); Mavo fetches it
here, with no model, and hands the result back to the Crew as untrusted data. Every source is a
fixed address: a Crew chooses only the words it looks up, never a URL. Results are small, cached
for a while, and each says where it came from.

The person can switch Open data off in Resources (nothing is contacted then). What a look-up sends
is only what it needs: the place, word, code or query it is about.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mavo.resources.feeds import FeedError, parse_feed

AGENT = "Mavo/0.1 (personal desktop assistant; https://github.com/himanshu2005-tech)"
MAX_BYTES = 3 * 1024 * 1024
CACHE_SECONDS = 600


class OpenDataError(RuntimeError):
    """A plain reason the look-up gave nothing; no result is made up."""


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    group: str
    gives: str  # one line for the person and the model
    usage: str  # how a Crew asks, for the model
    sends: str  # what leaves this computer
    home: str
    run: Callable[..., Awaitable[dict]]


class Lookup(BaseModel):
    """One look-up a Crew asks for."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source: str = Field(max_length=40)
    query: str = Field(min_length=1, max_length=300)
    to: str | None = Field(default=None, max_length=200)

    @field_validator("source")
    @classmethod
    def known(cls, value):
        if value not in SOURCES:
            raise ValueError("Unknown open data source")
        return value


# ---- fetching ------------------------------------------------------------------------------------


class Fetcher:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport
        self.cache: dict[str, tuple[float, object]] = {}
        self.polite: dict[str, asyncio.Lock] = {}
        self.last: dict[str, float] = {}

    async def get(self, url, *, params=None, data=None, kind="json", limit=MAX_BYTES, pace=0.0):
        """GET (or POST with `data`) a fixed public address; `pace` keeps the seconds a service
        asks for between requests (Nominatim: one a second)."""
        key = json.dumps([url, params, data], sort_keys=True, default=str)
        cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        host = httpx.URL(url).host
        # Only a service that asks for a gap between requests is queued; the rest run together.
        lock = self.polite.setdefault(host, asyncio.Lock()) if pace else contextlib.nullcontext()
        async with lock:
            wait = self.last.get(host, 0) + pace - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(
                    timeout=20, transport=self.transport, follow_redirects=True
                ) as client:
                    method = "POST" if data is not None else "GET"
                    async with client.stream(
                        method,
                        url,
                        params=params,
                        data=data,
                        headers={"User-Agent": AGENT, "Accept-Language": "en"},
                    ) as response:
                        response.raise_for_status()
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > limit:
                                raise OpenDataError("The answer was too large to use.")
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                raise OpenDataError(
                    "The service is busy; try again later."
                    if code == 429
                    else f"The service answered with an error ({code})."
                ) from None
            except (httpx.HTTPError, TimeoutError):
                raise OpenDataError("The service could not be reached.") from None
            finally:
                self.last[host] = time.monotonic()
        try:
            if kind == "xml":
                value = bytes(raw)
            else:
                value = json.loads(bytes(raw)) if raw.strip() else None  # 204: nothing there
        except ValueError:
            raise OpenDataError("The service did not answer in the expected format.") from None
        self.cache[key] = (time.monotonic() + CACHE_SECONDS, value)
        return value


FETCH = Fetcher()


def trim(text, size=400):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= size else text[: size - 1] + "…"


# ---- places and travel -----------------------------------------------------------------------


async def geocode(query, to=None, fetch=None):
    return {"places": await find_places(query, fetch)}


async def nominatim(query, fetch):
    """One search, as written."""
    found = await fetch.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": 3, "addressdetails": 0},
        pace=1.1,
    )
    return [
        {
            "name": trim(p.get("display_name"), 160),
            "lat": round(float(p["lat"]), 5),
            "lon": round(float(p["lon"]), 5),
            "type": p.get("type"),
        }
        for p in found or []
    ]


FILLER = re.compile(r"\b(campus|the|main|near|city|area|centre|center|branch)\b", re.I)


def place_tries(query: str) -> list[str]:
    """The name as given, then plainer forms: "Amrita Vishwa Vidyapeetham Chennai campus" is not
    a name OpenStreetMap knows; "Amrita Vishwa Vidyapeetham, Chennai" is."""
    plain = re.sub(r"\s+", " ", FILLER.sub(" ", query)).strip(" ,")
    words = plain.replace(",", " ").split()
    tries = [query, plain]
    parts = [p.strip() for p in plain.split(",") if p.strip()]
    if len(parts) > 2:
        # An address a model made up ("Name, street, suburb, Chennai"): the name and the town.
        tries.append(parts[0] + ", " + parts[-1])
    if len(words) > 2:
        tries.append(" ".join(words[:-1]) + ", " + words[-1])  # "name, town"
        tries.append(" ".join(words[:3]))
    return [t for t in dict.fromkeys(tries) if t]


async def find_places(query, fetch=None):
    """Places for a name: as written, then plainer forms, then Photon, which forgives looser
    names. Raises when none of them knows it."""
    fetch = fetch or FETCH
    for attempt in place_tries(query):
        found = await nominatim(attempt, fetch)
        if found:
            return found
    photon = await fetch.get("https://photon.komoot.io/api/", params={"q": query, "limit": 3})
    places = []
    for feature in (photon or {}).get("features") or []:
        props, (lon, lat) = feature["properties"], feature["geometry"]["coordinates"]
        name = ", ".join(x for x in (props.get("name"), props.get("city"), props.get("state")) if x)
        places.append(
            {
                "name": name or query,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "type": props.get("osm_value"),
            }
        )
    if not places:
        raise OpenDataError(f"No place called {query!r} was found; try its plain name and town.")
    return places


async def first_place(query, fetch=None):
    return (await find_places(query, fetch))[0]


async def route(query, to=None, fetch=None):
    if not to:
        # "A to B" (or "from A to B") written in one field.
        pair = re.match(r"\s*(?:from\s+)?(.+?)\s+to\s+(.+)$", query, re.I)
        if not pair:
            raise OpenDataError("Give where from (query) and where to (to).")
        query, to = pair[1], pair[2]
    fetch = fetch or FETCH
    start, end = await first_place(query, fetch), await first_place(to, fetch)
    data = await fetch.get(
        "https://router.project-osrm.org/route/v1/driving/"
        f"{start['lon']},{start['lat']};{end['lon']},{end['lat']}",
        params={"overview": "false"},
    )
    if data.get("code") != "Ok" or not data.get("routes"):
        raise OpenDataError("No driving route was found between those places.")
    best = data["routes"][0]
    return {
        "from": start["name"],
        "to": end["name"],
        "distance_km": round(best["distance"] / 1000, 1),
        "drive_minutes": round(best["duration"] / 60),
        "note": "Typical driving time with no live traffic; allow extra at busy hours.",
    }


def km_between(a, b):
    from math import asin, cos, radians, sin, sqrt

    dlat, dlon = radians(b["lat"] - a["lat"]), radians(b["lon"] - a["lon"])
    h = sin(dlat / 2) ** 2 + cos(radians(a["lat"])) * cos(radians(b["lat"])) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


async def nearby(query, to=None, fetch=None):
    """Named places of one kind near a place, from OpenStreetMap through Nominatim. (Overpass, the
    usual way, timed out from every mirror on this network.)"""
    kind = (to or "").strip().lower()
    if not re.fullmatch(r"[a-z ]{2,30}", kind):
        raise OpenDataError("Say what to find in `to`, such as atm, hospital, cafe or fuel.")
    fetch = fetch or FETCH
    place = await first_place(query, fetch)
    found = await fetch.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": f"{kind} near {query}", "format": "jsonv2", "limit": 15},
        pace=1.1,
    )
    near = []
    for p in found or []:
        spot = {"lat": float(p["lat"]), "lon": float(p["lon"])}
        km = km_between(place, spot)
        if km <= 5:
            near.append(
                {
                    "name": p.get("name") or kind,
                    "address": trim(p.get("display_name"), 140),
                    "km": round(km, 1),
                }
            )
    near.sort(key=lambda x: x["km"])
    return {"near": place["name"], "kind": kind, "found": near[:10]}


async def sun(query, to=None, fetch=None):
    fetch = fetch or FETCH
    place = await first_place(query, fetch)
    data = await fetch.get(
        "https://api.sunrise-sunset.org/json",
        params={"lat": place["lat"], "lng": place["lon"], "formatted": 0, "date": to or "today"},
    )
    times = data.get("results") or {}
    return {
        "place": place["name"],
        "times_utc": {k: times.get(k) for k in ("sunrise", "sunset", "solar_noon")},
        "day_length_seconds": times.get("day_length"),
    }


async def air(query, to=None, fetch=None):
    fetch = fetch or FETCH
    place = await first_place(query, fetch)
    data = await fetch.get(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params={
            "latitude": place["lat"],
            "longitude": place["lon"],
            "current": "pm2_5,pm10,us_aqi,european_aqi",
        },
    )
    return {
        "place": place["name"],
        "current": data.get("current"),
        "units": data.get("current_units"),
    }


async def pincode(query, to=None, fetch=None):
    fetch = fetch or FETCH
    kind = "pincode" if re.fullmatch(r"\d{6}", query) else "postoffice"
    data = await fetch.get(f"https://api.postalpincode.in/{kind}/{quote(query)}")
    offices = (data[0].get("PostOffice") if data else None) or []
    if not offices:
        raise OpenDataError(f"No post office was found for {query!r}.")
    return {
        "offices": [
            {k: o.get(k) for k in ("Name", "Pincode", "District", "State", "BranchType")}
            for o in offices[:10]
        ]
    }


async def flight(query, to=None, fetch=None):
    callsign = re.sub(r"\s+", "", query).upper()
    if not re.fullmatch(r"[A-Z0-9]{3,8}", callsign):
        raise OpenDataError("Give a flight's callsign, such as AIC101 or IGO6E2134.")
    fetch = fetch or FETCH
    data = await fetch.get("https://opensky-network.org/api/states/all", limit=12 * 1024 * 1024)
    for s in data.get("states") or []:
        if (s[1] or "").strip().upper() == callsign:
            return {
                "callsign": callsign,
                "country": s[2],
                "lat": s[6],
                "lon": s[5],
                "altitude_m": s[7],
                "on_ground": s[8],
                "speed_kmh": round(s[9] * 3.6) if s[9] is not None else None,
                "note": "Live position only: not schedules, gates or delays.",
            }
    return {"callsign": callsign, "found": False, "note": "Not in the air right now, or not seen."}


# ---- news and knowledge -------------------------------------------------------------------------


async def news(query, to=None, fetch=None):
    fetch = fetch or FETCH
    raw = await fetch.get(
        "https://news.google.com/rss/search",
        params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
        kind="xml",
    )
    try:
        items = parse_feed(raw)
    except FeedError as exc:
        raise OpenDataError(str(exc)) from None
    return {
        "headlines": [
            {"title": trim(i["title"], 200), "url": i["url"], "published": i["published"]}
            for i in items[:8]
        ]
    }


async def wikipedia(query, to=None, fetch=None):
    fetch = fetch or FETCH
    # Full-text search, not title prefixes: "Agniveer scheme" is the "Agnipath Scheme" article.
    found = await fetch.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 1,
            "format": "json",
        },
    )
    hits = ((found or {}).get("query") or {}).get("search") or []
    if not hits:
        raise OpenDataError(f"Wikipedia has no article for {query!r}.")
    title = hits[0]["title"]
    page = await fetch.get(
        "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_"))
    )
    return {
        "title": page.get("title"),
        "summary": trim(page.get("extract"), 1500),
        "url": (page.get("content_urls") or {}).get("desktop", {}).get("page"),
    }


async def hackernews(query, to=None, fetch=None):
    fetch = fetch or FETCH
    kind = query.lower() if query.lower() in {"top", "new", "best", "ask", "show"} else "top"
    ids = await fetch.get(f"https://hacker-news.firebaseio.com/v0/{kind}stories.json")
    stories = await asyncio.gather(
        *(fetch.get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json") for i in ids[:10])
    )
    return {
        "stories": [
            {"title": s.get("title"), "url": s.get("url"), "points": s.get("score")}
            for s in stories
            if s
        ]
    }


async def arxiv(query, to=None, fetch=None):
    fetch = fetch or FETCH
    raw = await fetch.get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": f'all:"{query.replace(chr(34), "")}"',
            "max_results": 6,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        kind="xml",
    )
    try:
        items = parse_feed(raw)
    except FeedError as exc:
        raise OpenDataError(str(exc)) from None
    return {
        "papers": [
            {"title": trim(i["title"], 200), "url": i["url"], "published": i["published"]}
            for i in items[:6]
        ]
    }


async def papers(query, to=None, fetch=None):
    fetch = fetch or FETCH
    data = await fetch.get(
        "https://api.openalex.org/works", params={"search": query, "per-page": 6}
    )
    return {
        "works": [
            {
                "title": trim(w.get("title"), 200),
                "year": w.get("publication_year"),
                "cited_by": w.get("cited_by_count"),
                "doi": w.get("doi"),
            }
            for w in data.get("results", [])
        ]
    }


async def books(query, to=None, fetch=None):
    fetch = fetch or FETCH
    data = await fetch.get(
        "https://openlibrary.org/search.json",
        params={"q": query, "limit": 6, "fields": "title,author_name,first_publish_year,key"},
    )
    return {
        "books": [
            {
                "title": b.get("title"),
                "authors": (b.get("author_name") or [])[:3],
                "first_published": b.get("first_publish_year"),
                "url": "https://openlibrary.org" + b["key"] if b.get("key") else None,
            }
            for b in data.get("docs", [])
        ]
    }


async def dictionary(query, to=None, fetch=None):
    fetch = fetch or FETCH
    try:
        data = await fetch.get(
            "https://api.dictionaryapi.dev/api/v2/entries/en/" + quote(query.lower())
        )
    except OpenDataError:
        raise OpenDataError(f"No dictionary entry for {query!r}.") from None
    entry = data[0] if data else {}
    return {
        "word": entry.get("word"),
        "phonetic": entry.get("phonetic"),
        "meanings": [
            {
                "part_of_speech": m.get("partOfSpeech"),
                "definitions": [
                    trim(d.get("definition"), 200) for d in m.get("definitions", [])[:2]
                ],
            }
            for m in entry.get("meanings", [])[:3]
        ],
    }


# ---- money, calendar, countries ----------------------------------------------------------------


async def currency(query, to=None, fetch=None):
    base = query.strip().upper()[:3]
    target = (to or "INR").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", base) or not re.fullmatch(r"[A-Z]{3}(,[A-Z]{3})*", target):
        raise OpenDataError("Use currency codes, such as query USD and to INR.")
    fetch = fetch or FETCH
    data = await fetch.get(
        "https://api.frankfurter.app/latest", params={"from": base, "to": target}
    )
    return {
        "base": data.get("base"),
        "date": data.get("date"),
        "rates": data.get("rates"),
        "note": "European Central Bank reference rates, updated on working days.",
    }


async def holidays(query, to=None, fetch=None):
    code = query.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", code):
        raise OpenDataError("Give a two-letter country code, such as IN or US.")
    year = int(to) if to and to.isdigit() else date.today().year
    fetch = fetch or FETCH
    data = await fetch.get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{code}")
    if not data:
        raise OpenDataError(
            f"Nager.Date has no holiday list for {code} (India is not covered); try wikipedia "
            f"'Public holidays in' the country."
        )
    today = date.today().isoformat()
    upcoming = [h for h in data if h.get("date", "") >= today] or data
    return {
        "country": code,
        "year": year,
        "holidays": [
            {"date": h.get("date"), "name": h.get("name"), "local_name": h.get("localName")}
            for h in upcoming[:15]
        ],
    }


# ---- alerts ------------------------------------------------------------------------------------


async def earthquakes(query, to=None, fetch=None):
    fetch = fetch or FETCH
    data = await fetch.get(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
    )
    words = query.lower()
    quakes = [
        {
            "place": f["properties"].get("place"),
            "magnitude": f["properties"].get("mag"),
            "time_ms": f["properties"].get("time"),
            "url": f["properties"].get("url"),
        }
        for f in data.get("features", [])
    ]
    if words not in {"all", "world", "any", "*"}:
        quakes = [q for q in quakes if words in (q["place"] or "").lower()] or quakes
    return {"last_24h_magnitude_4_5_plus": quakes[:12]}


async def disasters(query, to=None, fetch=None):
    fetch = fetch or FETCH
    raw = await fetch.get("https://www.gdacs.org/xml/rss.xml", kind="xml")
    try:
        items = parse_feed(raw)
    except FeedError as exc:
        raise OpenDataError(str(exc)) from None
    words = query.lower()
    if words not in {"all", "world", "any", "*"}:
        items = [i for i in items if words in i["title"].lower()] or items
    titles = list(dict.fromkeys(trim(i["title"], 200) for i in items))
    return {"alerts": titles[:12], "more": "https://www.gdacs.org"}


# ---- developers -------------------------------------------------------------------------------


async def release(query, to=None, fetch=None):
    name = query.strip()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", name):
        raise OpenDataError("Give a repository as owner/name, such as python/cpython.")
    fetch = fetch or FETCH
    try:
        data = await fetch.get(f"https://api.github.com/repos/{name}/releases/latest")
    except OpenDataError as exc:
        if "(403)" in str(exc) or "busy" in str(exc):
            raise OpenDataError(
                "GitHub allows 60 look-ups an hour without signing in; try later."
            ) from None
        # Many projects only tag their versions (python/cpython does): the newest tag is it.
        try:
            tags = await fetch.get(
                f"https://api.github.com/repos/{name}/tags", params={"per_page": 5}
            )
        except OpenDataError:
            raise OpenDataError(f"{name} was not found, or is private.") from None
        if not tags:
            raise OpenDataError(f"{name} has no releases or tags.") from None
        return {
            "repository": name,
            "latest_tags": [t.get("name") for t in tags],
            "note": "No published release; these are its newest tags.",
        }
    return {
        "repository": name,
        "release": data.get("tag_name"),
        "name": data.get("name"),
        "published": data.get("published_at"),
        "url": data.get("html_url"),
        "notes": trim(data.get("body"), 800),
    }


async def package(query, to=None, fetch=None):
    name = query.strip()
    registry = (to or "pypi").strip().lower()
    if not re.fullmatch(r"[@\w./-]{1,100}", name):
        raise OpenDataError("Give a package name.")
    fetch = fetch or FETCH
    if registry == "npm":
        data = await fetch.get(f"https://registry.npmjs.org/{quote(name, safe='@/')}/latest")
        return {
            "package": name,
            "registry": "npm",
            "version": data.get("version"),
            "summary": trim(data.get("description"), 300),
        }
    data = await fetch.get(f"https://pypi.org/pypi/{quote(name)}/json")
    info = data.get("info") or {}
    return {
        "package": name,
        "registry": "pypi",
        "version": info.get("version"),
        "summary": trim(info.get("summary"), 300),
    }


async def stackoverflow(query, to=None, fetch=None):
    fetch = fetch or FETCH
    data = await fetch.get(
        "https://api.stackexchange.com/2.3/search/advanced",
        params={
            "q": query,
            "site": "stackoverflow",
            "order": "desc",
            "sort": "relevance",
            "pagesize": 6,
        },
    )
    return {
        "questions": [
            {
                "title": trim(q.get("title"), 200),
                "answered": q.get("is_answered"),
                "score": q.get("score"),
                "url": q.get("link"),
            }
            for q in data.get("items", [])
        ]
    }


# ---- everyday ----------------------------------------------------------------------------------


async def food(query, to=None, fetch=None):
    code = re.sub(r"\D", "", query)
    if not 8 <= len(code) <= 14:
        raise OpenDataError("Give the barcode number printed under the bars.")
    fetch = fetch or FETCH
    data = await fetch.get(
        f"https://world.openfoodfacts.org/api/v2/product/{code}.json",
        params={
            "fields": "product_name,brands,nutriscore_grade,nutriments,ingredients_text,allergens"
        },
    )
    product = data.get("product") or {}
    if not product:
        raise OpenDataError("That barcode is not in Open Food Facts.")
    n = product.get("nutriments") or {}
    return {
        "product": product.get("product_name"),
        "brand": product.get("brands"),
        "nutri_score": product.get("nutriscore_grade"),
        "per_100g": {
            k: n.get(k + "_100g") for k in ("energy-kcal", "sugars", "fat", "salt", "proteins")
        },
        "allergens": product.get("allergens"),
        "ingredients": trim(product.get("ingredients_text"), 500),
    }


S = Source
SOURCES: dict[str, Source] = {
    s.id: s
    for s in [
        S(
            "geocode",
            "Nominatim",
            "Places and travel",
            "Where a place is (coordinates)",
            "query: a place",
            "The place name",
            "https://nominatim.org",
            geocode,
        ),
        S(
            "route",
            "OSRM routing",
            "Places and travel",
            "Drive time and distance between two places",
            "query: from, to: where to",
            "The two place names, then their coordinates",
            "https://project-osrm.org",
            route,
        ),
        S(
            "nearby",
            "OpenStreetMap places",
            "Places and travel",
            "Places nearby, such as ATMs, hospitals, cafés or fuel",
            "query: a place, to: what (atm, hospital, cafe, fuel, pharmacy...)",
            "The place and what you look for",
            "https://wiki.openstreetmap.org/wiki/Overpass_API",
            nearby,
        ),
        S(
            "sun",
            "Sunrise-Sunset",
            "Places and travel",
            "Sunrise and sunset times",
            "query: a place, to: a date YYYY-MM-DD (optional)",
            "The place's coordinates",
            "https://sunrise-sunset.org",
            sun,
        ),
        S(
            "air",
            "Open-Meteo air quality",
            "Places and travel",
            "Air quality now (AQI, PM2.5)",
            "query: a place",
            "The place's coordinates",
            "https://open-meteo.com",
            air,
        ),
        S(
            "pincode",
            "India Post pincodes",
            "Places and travel",
            "Pincode to area, or area to pincode",
            "query: a 6-digit pincode or a post office name",
            "The pincode or name",
            "https://api.postalpincode.in",
            pincode,
        ),
        S(
            "flight",
            "OpenSky Network",
            "Places and travel",
            "A flight's live position (not schedules or delays)",
            "query: callsign, such as AIC101",
            "Nothing but the request for live positions",
            "https://opensky-network.org",
            flight,
        ),
        S(
            "news",
            "Google News",
            "News and knowledge",
            "Latest headlines on any topic",
            "query: a topic",
            "The topic",
            "https://news.google.com",
            news,
        ),
        S(
            "wikipedia",
            "Wikipedia",
            "News and knowledge",
            "A short summary of a person, place or thing",
            "query: the subject",
            "The subject",
            "https://www.wikipedia.org",
            wikipedia,
        ),
        S(
            "hackernews",
            "Hacker News",
            "News and knowledge",
            "Top tech stories",
            "query: top, new, best, ask or show",
            "Nothing personal",
            "https://news.ycombinator.com",
            hackernews,
        ),
        S(
            "arxiv",
            "arXiv",
            "News and knowledge",
            "Newest research papers on a topic",
            "query: a topic",
            "The topic",
            "https://arxiv.org",
            arxiv,
        ),
        S(
            "papers",
            "OpenAlex",
            "News and knowledge",
            "Research papers with citations",
            "query: a topic or title",
            "The topic",
            "https://openalex.org",
            papers,
        ),
        S(
            "books",
            "Open Library",
            "News and knowledge",
            "Books by title or author",
            "query: a title or author",
            "The title or author",
            "https://openlibrary.org",
            books,
        ),
        S(
            "dictionary",
            "Free Dictionary",
            "News and knowledge",
            "What a word means",
            "query: one English word",
            "The word",
            "https://dictionaryapi.dev",
            dictionary,
        ),
        S(
            "currency",
            "Frankfurter",
            "Money and calendar",
            "Exchange rates",
            "query: from code (USD), to: to codes (INR or INR,EUR)",
            "The currency codes",
            "https://frankfurter.dev",
            currency,
        ),
        S(
            "holidays",
            "Nager.Date",
            "Money and calendar",
            "Public holidays",
            "query: country code (IN), to: year (optional)",
            "The country code",
            "https://date.nager.at",
            holidays,
        ),
        S(
            "earthquakes",
            "USGS earthquakes",
            "Alerts",
            "Earthquakes of magnitude 4.5+ in the last day",
            "query: a region, or all",
            "Nothing personal",
            "https://earthquake.usgs.gov",
            earthquakes,
        ),
        S(
            "disasters",
            "GDACS",
            "Alerts",
            "Floods, cyclones and other disaster alerts",
            "query: a country, or all",
            "Nothing personal",
            "https://www.gdacs.org",
            disasters,
        ),
        S(
            "release",
            "GitHub releases",
            "Developers",
            "A repository's latest release",
            "query: owner/name",
            "The repository name",
            "https://github.com",
            release,
        ),
        S(
            "package",
            "PyPI and npm",
            "Developers",
            "A package's latest version",
            "query: package, to: pypi or npm",
            "The package name",
            "https://pypi.org",
            package,
        ),
        S(
            "stackoverflow",
            "Stack Overflow",
            "Developers",
            "Questions and answers",
            "query: the problem",
            "The search words",
            "https://stackoverflow.com",
            stackoverflow,
        ),
        S(
            "food",
            "Open Food Facts",
            "Everyday",
            "Ingredients and nutrition from a barcode",
            "query: the barcode number",
            "The barcode",
            "https://world.openfoodfacts.org",
            food,
        ),
    ]
}


async def look_up(request: Lookup, fetch: Fetcher | None = None) -> dict:
    """One look-up, as a Crew gets it: the result or a plain reason, never an invented answer."""
    source = SOURCES[request.source]
    try:
        async with asyncio.timeout(45):
            result = await source.run(request.query, request.to, fetch=fetch)
        status = "ok"
    except OpenDataError as exc:
        result, status = {"error": str(exc)}, "unavailable"
    except TimeoutError:
        result, status = {"error": "The service took too long."}, "unavailable"
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > 9000:
        result = {"partial": text[:9000]}
    return {
        "source": source.name,
        "lookup": request.model_dump(exclude_none=True),
        "status": status,
        "result": result,
        "untrusted": True,
    }


def catalogue_text() -> str:
    """For a Crew's instructions: what it can look up and how."""
    return "; ".join(f"{s.id} ({s.gives}; {s.usage})" for s in SOURCES.values())
