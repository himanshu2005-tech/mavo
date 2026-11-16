# 0037 — Single-reader proposal access and observed read checks

Status: accepted

## Context

An Inbox Summary Crew had approved Gmail read/content access, but its sole member's
capability list was empty. The worker therefore received model access only, produced
a quiet answer without collecting Gmail, and passed deliverable-only setup verification.
Crew consent and member restrictions are separate authorities; inheriting everything
at runtime would bypass that separation.

## Decision

For a new proposal with one otherwise unconfigured reader, assign the proposed Google read
requirements to that member before presenting consent. Include desktop delivery only
when requested. Do not assign writes, expand explicitly restricted roles, copy private
access to multiple roles, or retrofit active Crew. Existing incomplete single-reader
drafts can bind explicitly selected Google reads when the user saves setup access.

First-run testing rejects required Google reads that no member can collect. Workers
record current-assignment, application-generated Google snapshot read counts. A setup
cannot pass merely because a model returned text when required reads were never observed.
Persist only resource names, counts, timestamps and correlation; no subjects, bodies or
message IDs in these check receipts. Authorized snapshots can use the shared cache.

Display names remain cosmetic. New planner guidance prefers compact, distinctive
identities with clear role cues. User-selected names and stable keys/IDs remain valid;
styling a name does not change resources, permissions, routes, budgets or team size.

## Consequences

Simple mail readers no longer silently lose their proposed Gmail requirements between
Crew and member. Deliberate restrictions still need explicit refinement. A read check
does not establish complete inbox coverage, summary quality, successful writes or future
provider availability. No migration or retroactive account/permission change is required.
