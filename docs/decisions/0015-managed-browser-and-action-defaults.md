# 0015: Managed browser sessions and reusable action defaults

Status: Accepted.

## Decision

Mavo uses an installed Edge instance with a separate persistent profile per Crew, never the user's Chrome profile. Actions expands each operation into saved defaults plus independent settings listed by Crew name. Enabled defaults apply to both manual actions and new description-based setups. Once setup questions are resolved, only requested operations without an existing Crew decision are copied, preserving exact limits and creating a new policy revision. Explicit Off is never overwritten; changing a default does not alter existing Crew. Google consent and exact resource grants remain separate.

Setup remains questionnaire → access review → Verify & add. Reused policies are labelled already allowed and remain editable. Inherited limits that do not cover requested sites/folders/apps/recipients block verification rather than silently expanding access. No model creates permissions.

`POST /crew/{id}/browser-session` validates the existing browser.open grant, scopes, global pause, and ownership before pausing/fencing automated work and executing an audited open. Manual sign-in holds the window for at most 15 minutes under that generation. Resume invalidates the hold and subsequent work uses the same persistent profile. Browser ownership is exclusive; other browser Crew wait without spending a model call. Closed, stopped, blocked, and idle-review windows release ownership. Authentication/CDN hosts must fit saved restrictions; Mavo does not bypass them for sign-in. Some sites may reject automation or require fresh login.

Password-manager exports, cookies, tokens, and existing Chrome profiles are not imported. Users sign in directly on the website. Profiles retain cookies/local storage on disk and need the user's Windows filesystem protections; they are not an encrypted application vault. Automated password/file filling remains blocked. Downloads, private networks, and unauthorized hosts remain blocked.

## Runtime contract

The shared model returns typed JSON actions; the application executes them under its existing grants and revisions. Opening/clicking/filling returns metadata only; reading page text requires browser.read. Prompts describe this protocol and do not tell Operator Crew that tools are unavailable. A false missing-browser-tools response can receive one corrective follow-up, charged against session/day budgets; a worker step never exceeds two model calls. Repeated failure becomes an honest model/protocol error, not a request for permission already granted.

`wait_for_next_review` ends a bounded review without marking the Crew blocked. Optional stable `notification_ids` persist reported identifiers (bounded to the most recent 1,000); repeated identifiers suppress repeated desktop messages. Notifications remain subject to their own grant and final revocation check. A read/analyze cycle normally costs at least two model calls: 48 reviews/day needs at least 96 calls, with more possible for navigation. No model is polled just to wait for the next interval.

## Verification

Tests cover independent named assignments, strict scope reuse, explicit denials, questionnaire gating, no accidental page-read grant, sign-in authorization/expiry, ownership waits without model charges, idle/closed-window release, false missing-tool correction, and duplicate notifications. An opt-in live AIRouter check uses at most three calls and an isolated example.com browser profile; it proves actual model/action integration, not a signed-in placement portal's behavior.
