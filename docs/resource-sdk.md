# Resource adapter SDK direction

This document fixes the intended Resource Pool contract. A small read-only Open-Meteo adapter now implements the initial `read(query)` boundary for local personal use with permission-checked destination lookups for Crew. General resource subscriptions remain in development.

A resource adapter will identify its resource type, capabilities, freshness policy, required grants, and normalized snapshot/event schemas. Adapters will be registered with the Resource Pool, not imported directly by Crew.

```python
class ResourceAdapter(Protocol):
    manifest: ResourceManifest

    async def read(self, request: ReadRequest, context: AccessContext) -> Snapshot: ...
    async def refresh(self, context: AccessContext) -> RefreshResult: ...
    async def subscribe(self, subscription: Subscription, context: AccessContext) -> None: ...
```

`AccessContext` will carry the user, Crew instance, purpose, and resolved permission decision. The Resource Pool must deny access before invoking the adapter. Writes use separate tool contracts and grants; a read connection never silently enables writes.

Snapshots include collection time, source time when available, expiry, content hash, and provenance. Refresh events describe collection mechanics; domain events describe meaningful changes. Signals subscribe to domain events, not every cache write.

Adapter inputs and outputs use versioned Pydantic schemas. Credentials are referenced by opaque credential IDs and resolved only inside provider code. Adapters must not return secrets or log sensitive payloads.

## Initial weather adapter

`OpenMeteoWeatherAdapter.search` returns candidate cities with state and country. Ambiguous queries require exact city/state/country; legacy ambiguous `read(query)` refuses to select the first match. `read_location` accepts validated coordinates and caches forecasts by approximate coordinates for 15 minutes. Production or paid redistribution must review provider licensing or use the planned credentialed WeatherAPI/Mavo proxy option.

`GET/PUT /api/v1/resources/access` persists strict independent Weather and Location flags, both false by default. All weather endpoints require Weather. `GET/PUT /api/v1/resources/location` requires Location and stores device coordinates independently of weather, rounded to three decimals with accuracy. Device-sourced forecasts require both permissions. Collection checks run before I/O and again before saving results; revoked access must not be replaced by cached information. Location revocation deletes device coordinates, GPS-derived selected weather locations, and cached forecasts. The UI automatically requests Windows/WebView geolocation when permitted, discards late callbacks after revocation, and reports Windows permission failures without silently substituting another place. No IP fallback is used.

Weather grants cover any public destination, not only the selected preview or the user's position. `resources/crew_weather.py` accepts up to three typed city/country or explicit destination-coordinate requests. It requires global Weather access and the Crew's `weather:read` grant before and after I/O. It cannot request device coordinates or overwrite the user's preview location. Exact-name city matching excludes unrelated prefix/airport results; same-named cities remain ambiguous. Errors and ambiguity are returned explicitly, never replaced with invented forecasts. Disabling Weather stops weather-enabled Crew and fences in-flight results.

The current Resources UI is automatic-only for computer location: no Set location, Detect, or manual city form. Opening the page or enabling Location starts detection. Returning to the page refreshes stale device data with a five-minute throttle; failed detection can retry when the user returns after fixing Windows permissions. Revoked access and late callbacks remain fenced. Typed destination searches remain available to authorized Crew and API consumers independently of computer Location.

## Bounded Google adapters

For current Google/GitHub setup, weather/location, server-owned registration, exact capability IDs, and adding actions, use [the provider README](providers/README.md). `ConnectorHub` extends existing Google behavior rather than replacing it, with fixed-authority bounded GitHub reads and a shared 60-second cache. GitHub supports interval reviews, not resource-event subscriptions; no general event engine is implied.

`GoogleResourcePool` also supports `drive` and `contacts`, using `google:drive:read` and `google:contacts:read`. See [setup and cost boundaries](plugins/SETUP.md#drive-and-contacts--read-only-plugins). These are bounded metadata/contact snapshots, not document downloads, write tools or full synchronization. Their account scopes, per-Crew grants, cache TTLs, persistent request ceilings and account-generation fences apply before model input. Neither currently emits domain change events.

## Curated public feeds

`ConnectorHub.feeds` implements `feeds:arxiv-ai:read` and `feeds:bbc-tech:read` without credentials. `POST /connectors/feeds/connect` accepts only `sources` from those two IDs; an empty list disables all. Global source enablement and exact per-Crew grants are independent. `GET /connectors/feeds/{source}` is an authenticated manual read. Background Crew collect only their saved source grants, with 15-minute shared caching, 20 metadata entries, 2 MB XML and total 15-second collection limits. Fixed source URLs, no redirects, rejected DTD/entities and no article fetches prevent arbitrary model-selected network access. Source changes are atomic; removing one fences/stops affected workers, clears their checkpoints/observations and preserves other providers. Add unknown-URL, network-error, malformed XML, caching, isolation and in-flight revocation tests before extending this catalog.

`GoogleResourcePool` implements shared authorized reads for inbox headers, primary-calendar events, and default incomplete tasks. Account OAuth consent and per-Crew read grants are separate boundaries. The shared cache lasts 60 seconds; baseline refreshes emit no domain events. Subsequent meaningful changes use persisted deduplication keys and worker cursors. These adapters are precursors to the general registry, not a claim of complete Signals or bidirectional sync. See [Google connection coverage](google-setup.md).

Message bodies use a separate `gmail:body` grant, bounded plain-text extraction, and no attachment downloads or HTML execution. Google mutations use the separate `GoogleActions` service with strict typed arguments, exact per-Crew grants, saved user action access settings, version checks for existing items, and outcome verification. Resource reads never execute writes.
