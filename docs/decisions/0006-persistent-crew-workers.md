# ADR 0006: Persistent Run/Stop workers

## Status

Accepted. Extends ADR 0005.

## Decision

Run starts work directly from a saved Crew configuration. Saving never starts inference. Store running intent separately from the current work state, so waiting for a schedule, input, or budget reset does not erase the user's decision to keep a Crew running.

Use a single in-process scheduler, two concurrent sessions, SQLite-backed claims with leases, generation fencing, bounded requests, and atomic daily budget reservations. Stop cancels local inference, invalidates the generation, and cancels queued reminders. Restart recovers expired claims while preserving checkpoints. A provider may still charge for inference already submitted before cancellation.

Give every Crew a distinct behavior profile. Schedule reminders and permission-controlled Computer measurements are deterministic and use no inference. Other types advance text tasks through validated checkpoints. They wait when they need user input or an unavailable integration; they cannot create tools, grant access, or claim external actions. Health and wellbeing context remains isolated per Crew.

## Interface

Use a restrained platform-style interface: system fonts, neutral surfaces, grouped controls, blue primary actions, a list/detail Crew view, and a compact creation sheet. Keep type, instructions, route, cadence, permissions, current state, and next work visible without decorative marketing copy.
