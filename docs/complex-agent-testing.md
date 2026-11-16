# Complex browser and Crew checks

These evaluations use real headless Chromium and Mavo's existing task runner,
Crew workers, permission engine, durable handoffs and source verification. Browser
pages are synthetic HTTPS fixtures intercepted inside an isolated browser context.
No personal browser profile, Google account, external submission or real Crew is
used or changed. Temporary data lives under the repository's ignored `.build/`.

## Reproduce without credits

```powershell
backend\.venv\Scripts\python.exe scripts\check-complex-crew.py --simulate
```

Three prepared specialists execute one Crew:

1. A portal navigator opens the test dashboard, finds a hidden internship link,
   reads the form, types keywords, selects Remote, clicks the local search button
   and reads the resulting listings. Mavo verifies exact quotations and observed
   URLs for both listings; opening a page alone does not count as success.
2. An application drafter receives discovery results and prepares a shortlist and
   cover-note draft, without browser or account-write capabilities.
3. An outcome reviewer receives discovery and drafting artifacts and checks them.
   A typed `review_verdict` is converted to the existing pipeline verdict protocol.

All browser traffic is intercepted. Only GET requests to `automation.mavo.test`
are served. Other destinations and all POST requests are aborted. Only this one
synthetic hostname bypasses DNS in the test process; production public-address
checks and policies are not weakened. Browser actions still pass through Mavo's
real capability and exact-domain checks. The page includes an untrusted malicious
advertisement; no permission is granted by page content.

The expected path uses five simulated model replies and seven audited browser
operations. This proves application behavior with deterministic model decisions,
not that an arbitrary live model will choose the same actions. Agent composition
uses a prepared validated plan to save credits; natural-language Core planning and
native map animation are not evaluated here.

## Optional live inference

```powershell
backend\.venv\Scripts\python.exe scripts\check-complex-crew.py --live-airouter --max-calls 6
```

Only inference is live. The script reuses the saved AIRouter route through the
existing provider abstraction, reading its DPAPI credential into memory. Every
HTTP attempt shares the six-request ceiling, including repairs. There are no
network retries or provider switches. Reports omit credentials, raw model replies
and private account content.

An optional reviewer-only protocol recheck has a **one-request** ceiling:

```powershell
backend\.venv\Scripts\python.exe scripts\check-complex-crew.py --live-airouter --review-only
```

This is a separate fixture-backed reviewer worker, not a continuation or full-team
replay. Count its request separately if running it after a team evaluation.

Reports are kept separate:

- `.build/complex-crew-simulated.json`
- `.build/complex-crew-live.json`
- `.build/review-contract-simulated.json`
- `.build/review-contract-live.json`

## Larger deterministic management tests

`backend/tests/test_crew_v2.py` includes an eight-agent graph: two parallel discovery
agents feed deduplication, eligibility, ranking, drafting, review and delivery.
Review deliberately asks for one fix. The draft and reviewer each run twice;
discovery and other completed stages run once. One authoritative TaskRun owns the
whole cycle, and a durable revision message records the repair. No paid calls are
made by this test.

Existing V2, orchestration, task and build-team suites additionally cover bounded
review rounds, fan-out/fan-in, cancellation, concurrent budget claims, pending
questions, permission revocation, idempotent delivery, restart recovery, membership
changes and private-artifact isolation.

The browser suite exercises 108-row pagination, page-size controls, delayed tables,
logged-out and CAPTCHA pages, multi-step forms, validation errors, password refusal,
rich editors, dependent fields, lazy loading, menus, tabs, iframes, overlays,
keyboard controls, drag/drop and cross-site/public-address restrictions. These are
real browser operations against fixtures, not live account integrations.

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/browser_agent backend/tests/test_browser_control.py backend/tests/test_multi_agent.py backend/tests/test_browser_proxy.py
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_crew_v2.py backend/tests/test_orchestration.py backend/tests/test_tasks.py backend/tests/test_team_build.py backend/tests/test_complex_browser_runtime.py
```

Chromium-backed tests skip explicitly if no supported browser is installed.

## Live evaluation — 9 November 2026

The first team evaluation used five AIRouter requests on `alibaba/qwen3.7-flash`.
All seven expected browser operations completed, both listing quotations matched
actual page reads, and discovery handed off to the drafting agent. The final
reviewer did not produce the legacy standalone verdict line, so Mavo correctly
blocked final delivery rather than declaring success.

Follow-up changes add a typed `review_verdict: pass | fix` checkpoint field, a
reviewer-specific schema/prompt and deterministic conversion to the existing
pipeline protocol. Legacy verdict lines remain supported. Typed repair cycles
are covered by the eight-agent regression. The original failed team report is
retained; a reviewer-only recheck must not be presented as a successful full-team
rerun.

The reviewer-only live recheck passed in one request, producing a validated typed
`pass` and its canonical pipeline verdict without proposing tools. Total live usage
for this evaluation was **six requests** (five for the team, one for the focused
reviewer recheck). The updated full workflow passes deterministic inference; it
was not rerun with live inference after the fix in order to keep spending bounded.
