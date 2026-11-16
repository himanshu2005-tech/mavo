# Chat failures and recovery

Mavo cannot guarantee that every model, provider or network request succeeds. It
must either return a validated answer or explain the failure without pretending
that a Crew/task was created or an external action succeeded.

## What the user sees

A failed general-chat turn keeps the submitted message visible, restores the
composer text, and shows an accessible error card with:

- The specific failure category and a plain-language explanation.
- The correct next step (publisher configuration, account sign-in, waiting for a
  rate limit, checking a selected planning route, or retrying a recoverable turn).
- A request reference for diagnostics.

Publisher API-key or credit problems are not presented as Google-login or personal
Crew-route problems. A lost connection is an **unknown outcome**, not proof that
nothing happened; the user is directed to check the conversation and Activity
before resubmitting. Waiting/unknown-outcome requests do not get a one-click paid
retry. Definitive recoverable failures can be resent deliberately; model POSTs
are never automatically replayed by the frontend.

Failed-message cards are held in the account's current in-memory workspace across
page navigation, and removed on logout or conversation deletion. They are not yet
restored as conversation messages after an app restart. Successful conversations
and existing Crew/task history retain their existing database persistence.

## Error transport and privacy

The publisher gateway returns the existing string `detail` plus a structured
`error` object containing `code`, `message`, `action`, `retryable` and `request_id`.
Older clients still receive readable detail strings. The desktop accepts only
known diagnostic codes and reconstructs user-facing text from its own catalog;
arbitrary provider response text, credentials and account tokens are not forwarded.

Covered failures include missing publisher setup, expired account identity,
provider credential rejection, insufficient credits, unsupported model/request
settings, rate limits, network/service outages, timeouts, invalid planning output,
empty answers, output truncation, request conflicts and incompatible desktop/Core
protocols. Unsupported or failed requests do not fabricate successful replies.

Ordinary invalid model JSON gets at most the existing one bounded correction.
For an existing setup draft, generation uses a narrower single-draft schema and
an explicit refinement instruction. Internal roles remain under one composition;
gym/mealtimes are planning constraints, not implicit requests for reminder Crew.
The repair repeats that instruction. Legacy configuration-patch replies remain
parseable for backwards compatibility, but are not advertised for a questionnaire.

If both responses fail yet the second contains a bounded, typed clarification
question, the draft-only fallback may retain that question. It replaces the model's
reply with an honest validation warning and discards all proposals, tasks, delegation,
connections, configuration changes and memory writes. The existing draft is not
activated or reconfigured. Invalid questions and active-Crew edits do not use this
fallback. Both publisher and explicitly selected planning routes share this rule.

Empty or truncated responses, provider HTTP failures and timeouts do not trigger
an extra paid request. Safe failure metadata is stored in the gateway's existing
`turns.response` JSON alongside `state='failed'`; no schema migration is needed.
An identical failed request returns its saved reason without repeating inference.
Old failed rows with no metadata are reported as unknown-cause service failures.

Core service status checks have a ten-second frontend deadline. A timed-out GET
is not retried twenty times. Offline chat offers a Check connection control;
health connectivity is not a guarantee that the model provider will answer.

## Apply changes in local development

```powershell
.\scripts\start-mavo.ps1 -Restart
```

This explicit command restarts this project's publisher service and desktop app.
It can interrupt an active chat or Crew execution; saved data is retained. It
verifies process/module ownership before stopping the listener, including the
Windows venv-launcher/base-Python parent-child case. An unrelated port owner is
never stopped. Without `-Restart`, already running processes remain untouched.

The development backend now owns local Core startup and bounded crash recovery,
including when launched through `dev.ps1`. No detached publisher terminal is needed.
Release builds do not auto-start a publisher; they use the deployed HTTPS gateway.

New managed Core launches write timestamped `core-managed-*.log` files under `.build/logs/`.
Look up the displayed request reference and safe failure code. Old logs are not
overwritten, and provider response bodies or chat text are not logged by the
new diagnostic handling.

A health failure occurs between desktop backend and Core, before AIRouter inference.
The frontend retries health automatically without replaying a message, and Check
connection forces a fresh probe and another bounded startup attempt. Negative
health results are not cached. Starting/recovering, exhausted startup attempts and
conflicting port owners have distinct messages. The header's Workspace ready badge
does not claim that a provider/model request has been tested.

## Verification

Conversation activity is strictly correlated by conversation ID. Global scheduled
Crew completions do not appear in unrelated or unsaved chats. The Updates inbox
retains pending workspace questions/permission requests; ordinary results remain
in the Crew update thread. API filtering and client-side scope checks both enforce
this, including when switching chats while a request is in flight.

Calendar/task-aware Schedule Crew proposals at one daily time use the existing
1440-minute AI review path, preserving the selected time. Plain reminders remain
deterministic. Multiple AI review times are still rejected instead of silently
discarded. No grant, write policy or existing Crew is changed by normalization.

Publisher failures now retain allowlisted validation reasons alongside field paths
and error types, never raw model output. Older failures retained only the field path
and error type; their exact validator reason cannot be conclusively reconstructed.
The daily-planner fix is a reproduced contract defect, not proof of any historical
model response's exact rejected JSON.

Draft follow-up regressions in `backend/tests/test_daily_planner_followup.py` cover
two roles remaining in one unactivated Crew, summary-only mail, retained routine
constraints, a corrected operation conflict, question-only fallback after two bad
responses, and parity for a user-selected planning route. No mail/calendar writes
or model credits are involved in those tests.

A bounded live check on 9 November 2026 used two requests to the configured
publisher AIRouter model: an initial draft-copy test returned an invalid batch;
after adding draft-mode instructions, a replay of the saved failed-turn context
returned a schema-valid single proposal in one call with no task or configuration
change. Schema narrowing and the fallback were subsequently regression-tested
locally, without another paid call. This is planning validation, not verification
of live email/calendar execution; the real saved draft was never modified.

`scripts/check-planner-followup.py` validates saved context without inference by
default. Explicit `--live --max-calls 1 --reference <request-reference>` uses the
configured publisher credential in memory for a bounded inference check only.
It prints diagnostics, not prompts, model output, account data or secrets, and
does not activate Crew or run tools. Live checks spend provider credits.

Account startup uses a separate local-service readiness gate. Transient connection
failures retry every 500 ms during an eight-second startup grace period (individual
status requests have a four-second deadline). They show “Starting Mavo”, not a false
signed-out state or duplicate raw fetch errors. Sustained failures expose one
actionable connection error and Retry, with subsequent automatic checks every five
seconds. Only a validated backend status can display Sign in or unlock the workspace;
network errors never clear account/workspace state. The confirmed signed-out status
is passed into the login card instead of triggering another immediate status fetch.
`src/lib/sessionStartup.test.ts` and `scripts/check-startup.py` cover warm-up,
prolonged outages, retry, signed-in/signed-out startup and module-load recovery using
isolated fixtures, not real Google account changes.

`backend/tests/test_core_errors.py` tests provider failure categories, redaction,
durable failure reasons, duplicate requests, empty/truncated responses, legacy
gateway compatibility and the authenticated desktop boundary using HTTP fixtures.
Publisher, planner, API and shared-memory regressions cover existing behavior.
Frontend API/controller/error-card tests cover message retention, account
isolation, explicit recovery and bounded timeouts. These checks use no paid
inference and do not sign in to or mutate real user accounts.

The historical generic “could not finish this turn” message hid the original
upstream cause. A healthy service and two historical `failed` rows were observed,
but those older rows had no error metadata, so their exact causes cannot be
reconstructed from the available records.
