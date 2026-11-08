# 0014: Keep chat setup as drafts until verification

Status: Accepted. Supersedes immediate publication in ADR 0011.

Chat and the natural-language builder persist setup using the explicit `draft` lifecycle status. Drafts retain identity, dialogue, requested access, and user-selected settings across restarts but are excluded from the Crew registry, counts, delegation targets, and executable workers. Storage uses the existing instance record to preserve foreign keys and scoped configuration; draft records are not registered Crew.

The response contract separates `draft` from published `crew`. `GET /crew-drafts` and the main conversation expose saved setup independently. The client keeps separate draft state and cannot count a draft even if one arrives through a stale response.

The setup UI presents questions before access settings. While any questions remain, it hides connection, resource, action, notification and verification controls and defers access-policy loading. The backend rejects premature access submissions without modifying grants. Finishing the questionnaire reveals the access-review stage; it does not publish the draft.

Verify & add is an explicit user setup operation. Deterministic checks cover unanswered questions, selected connection and stored credential, required account capabilities, per-Crew resource/action grants, notification access, trigger settings, and weather budgets. A successful check atomically changes draft to active and records configuration/authorization verification. It does not prove remote website behavior, API credit, or external task completion. Models cannot publish themselves or supply permissions. Refining a draft invalidates its previous confirmation. Activation remains subject to the global pause policy.

Migration 0013 preserves never-run incomplete legacy setups as drafts without deleting their content or dialogue. Existing work history and previously used Crew remain intact.

Deletion is a confirmed user operation that stops a Crew, cancels/fences pending work, removes scoped settings and saved sessions/reminders, and clears planning references. Existing external actions cannot be undone; action audit receipts are retained. Late model results, cached planning retries, and stale frontend polling cannot recreate the deleted item. A separate Discard setup operation uses the same deletion boundary for drafts.
