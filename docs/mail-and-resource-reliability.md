# Mail and resource troubleshooting

## What an inbox check actually means

Google account consent, Crew grants and each member's restricted access must all agree.
Reading headers does not imply reading email contents, sending mail, changing Calendar,
or editing Tasks. Mavo records current-assignment read counts and, for supported mail
triage, a per-message importance decision. Source text is untrusted data, never permission.

Read-only inbox readers publish important decisions through the Crew's saved desktop
permission. Calendar/Tasks planners publish actual confirmed changes, not setup previews
or claims that a proposed event was created. Quiet reviews remain in history. A resource
failure, denied permission or malformed answer must not silently become `Nothing new`.

Actionable Calendar/Tasks mail decisions must resolve to `needs_details`, `planned` or
`already_tracked`. Missing details produce a task-correlated chat question, retained mail and
a Crew-level desktop request. Such a checkpoint cannot also execute proposed changes.
Claims of planning require current-account, current-generation action receipts for each
required service. Claims of existing duplicates require IDs from the current supplied
Calendar/Tasks snapshot. Setup previews qualify only inside a validated private setup test,
never as real-world execution. These checks establish tool outcomes, not perfect semantic
matching or flawless model judgement.

Read-only mail cards use direct summaries plus a short literal authorized message excerpt,
so supplied timing is visible even if a model's summary omits it. A newer email without a
date/time cannot silently borrow an older message's time merely because the subjects match.
The planner must clarify it. Existing already-consumed emails are not automatically replayed.

An event-based Gmail Crew polls the shared account resource about once a minute while
Mavo is open and the computer is awake. That poll uses no model calls until matching new
mail exists. Detection plus inference plus desktop delivery is not instantaneous push.
An interval-based planner still checks only at its saved interval. Scheduling from the
same mail therefore need not happen at the same moment as an important-mail alert.

## Pickup-email regression

The observed example contained a direct pickup request, a bus-stand location and
`tomorrow 1PM`. The Google read and event delivery succeeded, but the worker returned
`Nothing new` without a notification. The event/manual-member handoff and optional
notification path permitted that answer to complete without a per-email decision.

Current mail reviews require decisions for supplied IDs before completion. A direct
request should be considered against the user's criteria, with relative dates interpreted
against the message timestamp and local timezone. The model can still misclassify mail;
its stored reason makes that inspectable. Missing event duration or other materially
required information must be clarified, not invented. No test should create an arbitrary
real appointment merely to prove Calendar access.

Changes apply after restarting the backend. Already-consumed emails are not replayed or
unmarked automatically. To test a fresh event after restart, send a new uniquely titled
message; a generic Check now must not duplicate old side effects by resetting cursors.

## Documented resource windows

- Gmail: rolling 20 inbox headers; up to ten unseen headers and three bodies in a model
  step. Event-triggered inputs are their exact pending batch (up to three), even if those
  messages no longer appear in that rolling snapshot. Attachments are not downloaded.
- Calendar: primary calendar, next 30 days, bounded pagination (up to 500 events).
- Tasks: incomplete tasks in the default list, bounded pagination (up to 500 tasks).
- Contacts: up to 100 saved contacts, names/emails/phone numbers. Not a full directory,
  Other Contacts sync, contact writes or arbitrary People API access.
- Drive: up to 100 recently modified files, metadata only. No content downloads or writes.
- Weather: explicit arbitrary destination support independent of device location; ambiguous
  place names require city/state/country. Shared forecasts are cached; malformed responses
  and provider outages are not treated as valid empty forecasts.

The model may receive a smaller bounded snapshot than the resource pool contains. These
are initial supported windows, not claims of exhaustive account search. Contacts/Drive
have refresh and daily request ceilings; successful consent does not bypass API enablement,
Google quota, revoked permissions or provider outages. Never paste secrets into chat or logs.

## Verification and launch

Tests use fixtures unless explicitly identified as live checks. A successful read-only
Google request proves current access, not successful future writes or perfect classification.
Paid model tests need a specified route and bounded request count. Existing scheduled
Crew may independently continue running while diagnostics are performed.

Restart with the single PowerShell launcher from the repository:

```powershell
.\scripts\start-mavo.ps1 -Restart
```

Restart preserves saved accounts, routes and Crew, but interrupts in-flight work; use it
when no consequential operation is executing. No account reset or reconnect is required
for these mail-review changes.
