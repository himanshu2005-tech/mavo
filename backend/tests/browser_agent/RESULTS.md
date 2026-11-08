# Browser agent: test results

Run on 3 Nov 2026. Real `DesktopActions` browser (Edge, Mavo's own profile) in every row.

| Suite | How | Result |
|---|---|---|
| Local scenarios | Fake sites served through Playwright routes, no model | 21 / 21 pass (`python -m tests.browser_agent.report`) |
| Backend unit + end-to-end | `pytest tests` | 429 pass |
| Frontend | `tsc -b`, `vitest`, `vite build` | clean, 44 pass |
| Model in the loop | Real planner model (AIRouter, qwen3.7-flash) on real sites | 3 / 3 pass, see below |

## Local scenarios (no model, seconds each)

| Area | What is checked |
|---|---|
| Placements | Read 108 jobs over several pages (12 / 12 open jobs found); page-size control; table that fills in after 7 s; logged-out portal reported as a sign-in wall, not "no jobs" |
| Forms | 11 field kinds filled and submitted; empty submit, read the errors, fix, resubmit; password and file fields refused, password never shown to the model |
| Multi-step | Three-step wizard with dependent dropdown; stale element refused with a clear message |
| Shop | Cookie wall, custom dropdown, shadow-DOM coupon |
| Account menus | Find Credits in the profile menu ($0.15 raises the alert, $3.40 does not); destructive controls untouched; logged-out reported |
| Page behaviour | Lazy list (10 items, 50 after scrolling); 6 s slow site; flaky "Failed to load" retried; tabs and accordion; links stay in one tab; iframe fields; hover-only menu; search box + Enter; "Are you sure?" accepted, "Delete account?" not |

## Model-driven runs (real sites)

| Task | Result | Time | Model calls | Actions |
|---|---|---|---|---|
| Fill and submit the httpbin pizza order form | PASS, server echoed every field | 105 s | 3 | 12 |
| Open quotes.toscrape.com, go to page 2, name the first author | PASS (Marilyn Monroe) | 69 s | 3 | 4 |
| Placements portal, scan all pages, report open jobs | PASS (CEI INDIA, BTech 2027, apply before 3 Nov 2026) | 430 s | 12 | 8 |

Also passed earlier: Wikipedia, Chennai population (3 calls, 120 s).

## Found by testing and fixed

- Replies with more than 3 actions were rejected; limit is now 16 and an invalid reply gets one repair attempt.
- A failed step in a batch no longer lets the final submit click run.
- Invented CSS selectors (`a:contains('More')`) are translated, or refused with "use the ref".
- Truncated page reads and stale refs made the portal task loop; the newest read now gets the memory budget and a read follows every page-changing batch.
- Empty tables, shadow-root errors and the "0 Jobs Listed" skeleton screen no longer produce silent wrong answers.

## Projects, Blender and the new browser actions (`tests/test_projects.py`, `tests/test_multi_agent.py`)

Checked on this machine with Blender 5.2 and Edge, not mocked:

| Check | Result |
| --- | --- |
| Blender headless: build a cube, save `drone.blend` in the project | passes, file listed as a changed file |
| Python script: runs in the project, no keys or tokens in its environment, runaway stopped at the limit | passes |
| Junction placed inside a project: not listed, read, written, hashed or previewed | passes (found and fixed by this test) |
| Run waits for approval; wrong hash, or scripts changed since the request, refused; trusted project runs without asking | passes |
| Crew proposes a run, waits, is approved, wakes with the printed output; denial reaches the Crew too | passes |
| Drag onto an element, drag by pixels (slider), hover menu, double-click on a real page | passes |
| Double-click on a `mailto:` link is refused | passes |

Real agent (the user's `qwen3.7-flash` connection), "Create a drone model in Blender and open it", copies of the data, Blender window moved off-screen: project made, script written, approval asked, Blender ran headless and saved `drone.blend` (119 KB), a second approval opened its window, which stays open independently of Mavo. It recovered from a script that failed on its first try using Blender's real error. 5 steps, about 5½ minutes.

Found by testing and fixed:
- Project actions were not recognised as local (they asked for an account); file content was trimmed like a name (it changed scripts); the file listing followed a junction.
- Blender exits 0 when a script crashes, so the agent was told the run had worked. Runs now pass `--python-exit-code 1`.
- A small model cannot reliably put a whole script inside a JSON string (a missing comma, an unescaped quote: the reply was rejected twice and the Crew stopped). A file now goes beside the JSON as a fenced block labelled with its path (` ```file:scripts/build.py `), and the block wins over whatever `content` says: models write `""` or "see the attached file" there.
- The same model put the script in `file` instead of `script`, or asked Python to open a window. Plain mix-ups are taken as meant instead of rejecting the whole reply.
- Asking again for a Blender window that is already open reports it instead of opening a second one.
- A watching Crew with desktop alerts on never alerted: the agent wrote "Status: LOW (below 10)" in its report but did not fill the separate `notification` field, and only that field reaches the desktop. Real model, 3 reviews with a $0.66 balance: 0 alerts. A plain instruction at the end of its prompt (only `notification` reaches the desktop, with an example) gave 3 alerts in 3 reviews. It is still the model's decision, so it is not guaranteed.
- Every chat message failed with "The Crew setup changed" once 17 capabilities were connected (GitHub plus a dozen feeds): the chat's context was capped at 16. The cap is now the number of capabilities that exist, and the real reason for that sentence is written to the log.

## One-time tasks, helpers and automations (`tests/test_tasks.py`, `tests/test_automations.py`)

The real assistant and the user's real agent connection (`qwen3.7-flash`), a real browser on a fresh profile, copies of the user's data: "Find out the latest stable Python 3 version number on python.org, have it double-checked, and tell me just the version number."

| Run | What happened |
| --- | --- |
| First | The assistant started a task (no Crew drafted) with two agent steps. The first found 3.15.2. The second failed: the two agents ran together and Mavo has one browser, and "browser busy" was read as "finished without an answer". Fixed: steps that use the browser take turns, and a busy browser is waited out. |
| Second | The assistant's reply failed validation (a strict plan format, and no second chance) and nothing started. Fixed: plans are forgiving where the intent is clear, and the assistant service asks for one correction. |
| Third | Done in 164 s, but the answer was a checker's remark ("3.15.2 does not exist yet"): the assistant had made the think-only checker the final step, and the reading agent had run out of steps and handed over a status line. Fixed: a checker is never the answer (its verdict is only added when it finds a problem), agents are told their step count and to finish by the last, and helpers are told that what INPUT says about the present is newer than their own knowledge. |

Also found by testing: the worker runtime already had an attribute called `tasks` (its table of running crews) and the runner overwrote it; the HTTP handlers that start and stop a task must be async; a task must start only after the chat turn's transaction closes (the symptom was `database is locked`).

## Browser control, 5 Nov 2026 (`tests/test_browser_control.py`)

From the action log of two failed runs in the user's app: "cheapest train fares to Chennai" made 26 searches and opened no result (five searches before one read, so it saw only the last; the plan left `browser.open` out; no departure city, so it searched ten cities), and "Create Student form" clicked the same controls repeatedly, four clicks timed out under a covering tip, and it never typed.

Fixed: the assistant asks for a detail that decides the answer before starting (and a task started in the same reply as such a question is held back); a search returns its results (title, real address, snippet); an agent that searches always gets `browser.open` and `browser.read`; one page is opened per step and read in full (the agent is told its summary is its notebook); a click under a banner presses Escape and retries, then names what covers it; typing into a web app's own editor goes key by key.

Real run afterwards (the user's `qwen3.7-flash`, copies of the data): the assistant asked "What is your departure city?" (3 of 3 trials asked or were held back), then on "From Hyderabad, this Saturday" the task finished in 269 s with 8 of 12 calls: the cheapest sleeper fares with train numbers, read from ixigo and redBus. It still guessed two URLs (a 404, a redirect) before using search results. Fares not independently checked.

## Known limits

- Model latency is 40 to 77 s per call with qwen3.7-flash. The portal task needed 12 calls, so a long scan takes minutes.
- The portal answer was correct but its closing text was cut off mid-sentence.
- Drag and drop and file upload are not supported.
- Mavo cannot drive your own browser profile, the browser blocks it. It uses its own profile, and Google sign-in goes through a plain window.

## Sign-in and session validation (`python -m tests.browser_agent.login_validation`)

Run on the real Edge with an empty profile for each site, and on a copy of the app's own profile.
14 of 14 checks pass.

| Check | Result |
| --- | --- |
| Signed-out reading: github, news.ycombinator, wikipedia, stackoverflow, airouter.in, linkedin, accounts.google | all read "signed out" |
| placements.haveloc.com with no login | human check ("captcha") |
| example.com, httpbin.org (no sign-in or sign-out control) | "unknown", never "signed in" |
| Session-only sign-in, window closed by Mavo | still there for the background browser, and after it restarts |
| Session-only sign-in, window closed with the X | still there, both times |
| Sign-in window position | inside the usable screen |
| The app's own profile, placements portal | opens as "Jobs" with no sign-in wall; saved login found with the browser closed and while it runs |

Found and fixed while validating: a sign-in kept only in a session cookie was lost whenever the
sign-in window closed (now kept, see ARCHITECTURE.md "A sign-in has to survive the window being
closed"); the sign-in window could open off the screen; the saved-login check said "none saved"
while a browser had the cookie file locked (now "cannot tell", or the running browser is asked).

Not verified: a real Google sign-in through the sign-in window (needs an account); "looks signed in"
(`likely`) is a heuristic for portals with no visible sign-out control, and only opening the site
proves a login.

## AIRouter and similar sites, as a person would use them (agent connection: the user's own, on copies of the app data and profile)

Sample run ("Try it first") and scheduled review, signed in where a login exists. Slow and billed, so these were run by hand.

| Site and task | Result |
| --- | --- |
| airouter.in, signed in: read the credit balance (apex redirects to `www`; menu to Credits) | Balance read, 2 model calls, about 1 minute |
| The same Crew as a scheduled Crew, 6 reviews in a row | 6 of 6 re-visited the site and reported the balance, 2 model calls each |
| placements.haveloc.com, signed in, as a scheduled Crew, 2 reviews | Both reviews read the portal and listed the open jobs |
| python.org: latest Python release (menu to Downloads) | 3.15.2 |
| github.com: Team plan price (menu to Pricing) | $4 per user per month |
| airouter.in pricing (public side) | Pricing summarised |
| wikipedia.org: English article count (apex redirects to `www`) | 7,254,000+ |
| news.ycombinator.com: top story | Title read |

Found by testing and fixed:

- A Crew allowed `airouter.in` could not read the page it was sent to, `www.airouter.in`. `example.com` and `www.example.com` are now one site.
- Every identical browser action in a later review was answered with the first review's saved result, so a scheduled Crew never re-visited its site. Each step now has its own key.
- `--restore-last-session` on the background browser could stop the next run's browser from starting (python.org Downloads, then github.com hung for the full limit). Removed there; the sign-in window keeps it.
- A browser that had been closed but was still leaving was read as "your sign-in window is open", so every later run said "please sign in". The two kinds of browser are now told apart, and a leftover is cleared before a new one starts.
- A correct answer with `"next_step": null` was reported as "the agent connection did not answer".
- A login-looking page met while opening a signed-in site stopped the rest of the batch and, seen twice, asked the person to sign in.
- An agent that clicked where it should have navigated made the run stop and ask the person to enable clicking; it now gets a plain refusal first.
- Finding the "billing" page followed the `mailto:billing@airouter.in` link on AIRouter's Credits page, twice, and started the person's mail app. Link addresses were scored as words, and nothing excluded links that start another program. `mailto:`, `tel:` and app links, and buttons labelled with an email address, are no longer offered, scored or followed (a direct click on one is refused with a plain reason).
- The same search carried on after reaching `/settings/credits` (the goal word "billing" never appears on a page called "Credits") and clicked "Refresh credits" and "Add Credits", a purchase button. A synonym of the goal appearing as a whole word now counts as arriving, and "Add Credits/funds/money/balance", recharge, upgrade and renew join the controls that are never clicked.
- Before these fixes the scheduled AIRouter check passed about half the time, sometimes by quoting the first review's page.
