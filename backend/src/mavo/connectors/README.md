# Mavo account connectors

See the [provider and action development guide](../../../../docs/providers/README.md) for setup, source registration, authorization, tests, and extension instructions.

`catalog.py` is the server-owned GitHub capability/action allowlist. `hub.py` handles accounts, secure token references, normalized reads, caches and disconnection; `actions.py` implements typed provider actions; `api.py` exposes authenticated local endpoints. Google retains its existing OAuth implementation in `google_auth.py`. All writes run through the shared permission/audit executor. A connector being registered is not a successful account connection. `retired.py` only cleans up obsolete grants and credentials during upgrades; it cannot connect or execute tools.
