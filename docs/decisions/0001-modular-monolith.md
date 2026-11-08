# ADR 0001: Start as a modular monolith

- Status: accepted
- Date: 2026-10-15

## Decision

Mavo will use one desktop host, one local Python backend, and one primary database while maintaining explicit internal module boundaries.

## Rationale

This minimizes local operational complexity, keeps authorization transactions coherent, and avoids one model process per Crew. Provider and persistence interfaces preserve paths to PostgreSQL and hosted workers later.

## Consequences

Modules must not bypass domain interfaces merely because they share a process. Work that truly needs remote execution can be extracted after its contract and operational requirements are proven.
