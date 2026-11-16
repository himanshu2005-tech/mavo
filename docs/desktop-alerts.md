# Desktop alert delivery

Mavo's always-on desktop icon expands into a readable notification card when a result arrives.
An unread card no longer automatically folds into a dot after a short one-line pill. It does
not steal keyboard focus, and the main window being open does not suppress it. These are
Mavo overlays, not Windows Notification Center toasts.

Settings → **Desktop alerts → Test alert** reopens an existing unread native update or shows
an explicitly labelled local preview. It invokes no model, changes no provider data and does
not change mute. Close/Done ends the preview. This is a display diagnostic, not evidence that
a Crew successfully read mail or changed Calendar/Tasks.

Reports use the shared [LaTeX-style reader](report-formatting.md), including legacy
line-break repair and explicit source links, without visible formatting controls.

The notification card renders every supplied list item, record and table row in a
keyboard-focusable scroll pane, without a non-interactive “+ more” truncation.
Its header and footer stay visible while the contents scroll. **Full screen** opens
a larger reader across the current monitor's usable area with larger text. Use the
restore icon or **Escape** to return to the compact card; Close folds it away and
Done dismisses it. Reading mode holds incoming popups in the queue until you return
so another update does not interrupt the digest. It does not mark them as read.

## Delivery rules

- The Crew must have **Tell me on my desktop** enabled, be active and running.
- A completed source-verified feed digest containing new evidence queues an alert
  deterministically. It does not depend on the model filling its optional
  `notification` field. The parent Crew owns delivery; read-only specialists do
  not need separate permission to independently publish notifications.
- Current-assignment typed important-mail results use the same parent-owned delivery
  principle. A reader's missing independent notification capability must not suppress its
  Crew's approved alert. Account, read grants, assignment and notified message IDs are checked.
  Calendar/Tasks planners report confirmed changes or correlated questions, not a triage
  summary pretending the schedule was updated.
- Completion, the desktop outbox entry and evidence deduplication state commit
  together. Private setup tests never publish their results on the desktop.
- The status window checks its authenticated loopback API every second, with
  four-second request deadlines and no nested retry loop. With an empty queue,
  a ready desktop normally picks up a committed update in about one second.
  This is not a hard real-time guarantee.
- An unread popup cannot indefinitely block another. Each queued update gets at
  least nine seconds before the next one replaces it. Superseded unread updates
  remain in **Missed**, not dismissed. Pill/card/menu transitions keep one consumer
  mounted and do not replay the same update. A question with an in-progress reply
  is not replaced; new cards cannot inherit another task's reply form state.
- Muting prevents new popups without stopping Crew. Old updates (over 15 minutes)
  are available in Missed instead of replaying a day's notifications on wake.
  Pending Crew updates remain available after midnight.
- On startup, the latest source-verified digest from an older runtime can recover
  a missing outbox entry without re-running the agent or spending model credits.
  Its original completion timestamp is retained; old digests appear in Missed.
- Empty/no-new reviews stay quiet. Unverified, cancelled, stale-generation or
  unauthorized work cannot create a successful-digest alert.
- Native resize/show failures do not leave a phantom delivered popup; the consumer
  retries. Connection trouble is shown on the desktop status UI.

The computer must be awake and Mavo running and signed in. A quit, suspended
runtime, OS failure, disabled Crew or deliberate mute prevents instant popups.
An alert queued in SQLite is not proof that a person saw or acknowledged it.

## Verify locally

1. Restart the development app once using `./scripts/start-mavo.ps1 -Restart`
   (this interrupts active work but retains saved data).
2. Enable desktop updates for the News Crew; ensure the icon is not muted.
3. Check now with a source that has new items. When verified work finishes,
   the icon expands into a pill. Click it for the full digest.
4. Leave one update unread and deliver another. Both remain accessible; dismiss
   one and check Missed for the other. A genuinely unchanged check stays quiet.

## Regression checks

- `backend/tests/test_digest_delivery.py`: deterministic delivery with no worker
  notification permission/model field, repeated callbacks, rollback, same-evidence
  suppression, restart repair, midnight retention, revocation and private tests.
- `backend/tests/test_news_checkpoints.py`: real planner/worker/TaskRunner flow with
  fixture feeds and mocked model responses, followed by a quiet unchanged cycle.
- `src/lib/reminderDelivery.test.ts`: one consumer, queue progression, failed native
  show retry, mute, restart, dismissal, logout and preview isolation.
- `scripts/check-desktop-alerts.py`: real React status-window pill/card/Missed UI
  in Chromium, intercepted API fixtures and mocked native IPC. Requires Vite on
  localhost:1420. This is not a live Windows popup or provider verification.

No new database migration or credentials are required.

## News review failures and history

Current behavior uses [reference-bound source reporting](source-reporting.md): models
select fetched item IDs; Mavo attaches original metadata and preserves valid stories
in a clearly marked partial digest. Legacy citation replies remain compatible.

Mavo, not the model, maintains the delivered-item history. Recurring feed snapshots
are filtered before inference. A previous `NO NEW ITEMS` checkpoint is historical
context, not evidence that the current feeds are empty. A feed-only final report
with supplied new items must cite an exact observed quotation and the exact URL
from the same item. Missing or mismatched evidence gets the worker's one shared
checkpoint-repair attempt, charged to existing run/daily budgets. This is not an
extra unbounded retry policy. If repair fails, the run fails honestly; no successful
digest is published, and the Crew can issue a failure alert instead.

Collection is not reporting: only verified reported feed item IDs become seen
after the parent cycle commits successfully. Omitted and unselected items remain
eligible; failure does not consume them. This does not rewrite existing history
or restore items already marked seen by older runtimes.

A failed cycle preserves the prior successful report. For older failures that
cleared it, the Crew list projects the last successful TaskRun result for that
same Crew without changing saved state. The inspector labels it as a previous
report rather than claiming the failed run succeeded. If no verified report
exists, it explains the failure instead of saying it is awaiting a first result.

Handoff times are interpreted as UTC before local display. Transport `pending`
does not imply a running task: the inspector also uses the authoritative TaskRun
status. A pending result addressed to the user is “Result available,” not a claim
that it was read. The original transport state remains visible for inspection.

Regression tests use synthetic feed snapshots and fake model responses, including
the old-digest/no-citations failure, successful bounded repair, exhausted repair,
failure notification, preserved history, exact same-item citation matching and
unreported-item retention. These are not live provider or Windows popup tests.

### Citation copying and feed-only contracts

Feed-only readers receive a small checkpoint schema rather than irrelevant browser,
filesystem and weather schemas. Nonempty supplied feeds require nonempty evidence;
empty successful feeds still permit a quiet result. Initial setup previews explicitly
use current stories as the initial digest, not a fabricated no-new baseline. A bounded
data-only citation example is taken from the actual authorized input, never invented.

If a model reconstructs an article URL, Mavo can bind a verbatim headline quotation
of at least 24 characters to the observed URL only when it uniquely identifies a
single supplied story URL. Duplicate copies with the same URL do not add ambiguity.
An ambiguous quotation, fabricated text, or a link to a different supplied story is
not corrected. Browser/listing/action evidence is outside this adapter. The resolved
evidence still passes the normal assignment-scoped authorization and source verifier.
Corrected cited URLs replace the corresponding model URLs in summary/notification
text; this does not establish the factual accuracy of every generated paraphrase.

`first_run.feed_checks` retains only application-generated citation counts, correction
counts and unmatched indexes after disposable test workers are cleaned up. It stores
no raw model answers or article text. No migration or account changes are required.

`scripts/check-news-setup.py --simulate` runs the real setup path in a disposable
workspace with synthetic inputs. `--live-airouter --max-calls 1` reads the saved news
draft/route without changing it, fetches its curated public feeds and permits at most
one actual paid request; a needed repair is blocked by that test ceiling. The default
live ceiling is two. This test does not sign into other accounts or activate the real
Crew. Its reported reserved model calls can exceed actual HTTP requests if a repair
reservation is blocked by the test transport.
