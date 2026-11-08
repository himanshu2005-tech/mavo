# ADR 0002: Tauri-owned authenticated sidecar

- Status: accepted
- Date: 2026-10-15

## Decision

Tauri owns the FastAPI process, chooses a random loopback port and per-launch 256-bit token, passes configuration through environment variables, and terminates the process on desktop exit.

## Rationale

Loopback services can be reached by other local processes and browser contexts. A random token plus strict CORS protects application data, while Tauri ownership prevents orphan services and fixed-port collisions.

## Consequences

The webview must obtain connection data through a Tauri command. Browser-only development requires explicit environment configuration. Packaged builds must include the backend executable and migration assets.
