# ADR 0003: Persist Crew configuration before enabling execution

## Status

Accepted.

## Context

Users need to create and manage named specialist instances before the Resource Pool, Signals, and durable job runner are complete. Treating a saved configuration as an executing autonomous agent would be misleading and unsafe.

## Decision

Mavo persists Crew identity, template, responsibilities, autonomy intent, selected provider/model, status, and a per-session model-call ceiling. The initial permission set is limited to model invocation. Resources and triggers are empty and cannot be granted by this API. The UI explicitly states that background work is not enabled.

The initial 15 built-in manifests were Pydantic-validated and marked `configuration_only`. This restriction is partially superseded by [ADR 0005](0005-bounded-crew-sessions-and-reminders.md): bounded manual text sessions and daily reminders are now supported. Resource-backed autonomous execution still requires the planned permission-checked Resource Pool and Signals boundaries.

## Consequences

Users can create multiple independent instances of a template and safely prepare their configuration now. “Enabled” means eligible for future execution, not that work is currently running. Later migrations can add scoped memory and subscriptions without replacing instance identity.
