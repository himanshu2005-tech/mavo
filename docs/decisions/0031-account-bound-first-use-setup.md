# 0031: Account-bound first-use setup

Status: Accepted

## Context

The workspace previously opened directly after authentication. A fresh installation needed a
guided route from identity to model routes and optional resources without implicitly enabling
integrations or creating sample Crew.

## Decision

Place an onboarding gate after the existing verified-session gate and before the workspace.
Present it as a compact desktop setup dialog with a step sidebar, scrollable settings content,
and short controls rather than a landing-page hero or promotional card layout.
The steps are Welcome, Providers, Resources, then Complete. Reuse the real model-route form
and resource connection controls rather than separate mock setup widgets.

Store versioned progress in `WorkspaceSetting`, keyed by a hash of the server-owned Firebase
project/UID binding. The client submits only an action and expected revision, never an account
identifier or arbitrary completion state. Compare-and-set revisions prevent competing windows
from silently overwriting progress. Read/error states fail closed with retry.

Provider setup requires a configured saved route or an explicit skip. Resource setup is optional.
Advancing never grants resources/actions, starts work, installs a model, performs paid inference,
or connects an external account. Completing setup persists across restarts and sign-out/sign-in.

## Boundaries

This preserves the existing single-owner local workspace model. It does not implement multiple
simultaneously accessible account profiles: a different owner cannot inherit another user's data.
Switching the owner of a populated installation requires an explicit fresh workspace/reset.
Publisher configuration and Firebase authentication records are not personal workspace content.
Local sign-out/deletion does not guarantee remote OAuth grant revocation.

Google capability request limits derive from the server capability catalog, not a hardcoded
ten-item limit. The same bound applies to consent, Crew access and coordinator context, while
the capability allowlist still rejects unknown IDs. Google setup discloses all requested
services, including YouTube, before consent.
