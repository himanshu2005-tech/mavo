# ADR 0033: Crew-owned logical agents over the existing execution authorities

Status: accepted for the V2 implementation; release limitations are documented separately.

## Context

Previously a public Crew also identified one worker. Persistent pipelines linked separate Crew;
temporary software teams used TaskRun stages. Replacing either runtime would risk duplicate jobs,
lost approval/checkpoint state and widening permissions through delegation.

## Decision

Add `CrewAgent` identities beneath the existing Crew. Reuse TaskRun for dependency/cycle ownership
and CrewRun for bounded leased worker execution. A legacy member points at its existing Crew
worker. Explicit compositions use hidden internal execution adapters to isolate observations and
reuse the established action stack. These adapters are implementation details, not extra public
Crew or additional LLM processes. The scheduler never independently selects them.

Agent membership is validated, stable by role key, versioned through existing configuration edits
and changed only through authenticated proposal/application paths. Removed roles are retired.
Durable messages use canonical agent IDs and iteration-specific idempotency keys. Actual parent
grants, policy revisions and generations remain authoritative at execution boundaries.

## Consequences

Single-worker reminders and monitors preserve their deterministic path. Multi-agent work gains
the existing TaskRunner fan-out/fan-in, leases, recovery, approvals and cumulative budgets. No
separate queue is introduced. The internal adapter indirection must remain hidden from user-facing
counts, cleanup and permission controls; regression tests cover those boundaries.

V2 retains eight agents per Crew and eight public task assignments. Whole-Crew assignments expand
within the same TaskRun, with a bounded 72-step internal graph and deterministic fan-in boundaries.
Dynamic software-building identities
remain temporary and are not blindly converted into permanent Crew agents. Currency ceilings
fail closed for cloud routes until trustworthy pricing/usage accounting exists. Outcome evidence
and operation completion are deliberately distinct.

See [Crew V2](../crew-architecture-v2.md) for lifecycle, configuration, migration and release gaps.
