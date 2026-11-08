# Practical automations

Describe one of these in Chat or Create Crew. Answer the setup questions, review the exact access, then Verify & add. Keep Mavo running and Windows awake. These examples do not authorize cleanup, sending mail, applying for jobs, or other unrelated actions.

## Drive status every five minutes

“Check free space on my C: drive every five minutes. Show free GB and percentage on the desktop every check. Never delete files or run cleanup.”

Computer Crew → storage C: → each_check. Enable device counters and desktop updates. No model calls. Verified with actual Windows counters and the real attached desktop panel, plus isolated recurrence/dismissal tests. Unread old status reports are replaced by the latest.

## Quiet low-space warnings

“Warn me if C: has 5 GB or less free. Check every five minutes, but stay quiet while it is healthy. Tell me when it recovers. Do not clean anything.”

Computer Crew → storage C: → alerts → min_free_gb 5. Warns once on crossing; recovers above 6 GB to prevent threshold flapping. Tested using explicitly synthetic counter sequences; no actual drive was filled or altered.

## Watch reminder

“Remind me to wear my watch every day at 10 PM.”

Schedule Crew → daily 22:00. Uses no model calls. Appears attached to the desktop icon; Dismiss acknowledges it and Read aloud speaks it. Durable reminder timing/deduplication verified in an isolated workspace; previews are explicitly labelled.

## Portal updates

“Check my placements portal every 30 minutes and notify me only about new open jobs. Read only; never apply or submit.”

Mavo needs the URL, new-job criteria, browser open/read (possibly navigation), desktop updates, and an agent connection. Sign in manually in the Crew's separate Mavo browser, not Chrome. A round-the-clock two-call review needs at least 96 calls/day. Public-page open/read/analysis is a separate live AIRouter test; private portal login and site-specific extraction must be verified after sign-in. Some sites block automation. A model response can fail validation and must not be presented as successful work.

## Important college email

“When an email from my college arrives, highlight urgent deadlines and ignore promotions. Only read and notify; do not send or archive.”

Choose Gmail arrival, not a five-minute inference timer. Needs Google consent and Crew Gmail read access (body only when needed), desktop updates, and an agent connection. Sender filtering/deduplication and model-boundary handling are fixture-tested; these checks do not read or send real mail. Enable write tools only for a separate explicit responsibility.

## Reproduce checks

Run `backend\.venv\Scripts\python.exe scripts\verify-automation-examples.py` for isolated real device reads, reminders, and denied-access behavior. Run the ordinary pytest suite for warning/error/revocation cases. `scripts/check-browser-worker.py --live-model` makes at most three paid AIRouter calls in an isolated public-site browser profile. None of these create permanent sample Crew in your workspace.
