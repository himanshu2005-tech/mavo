# 0019: Multiple persistent MAVO CORE conversations

Status: Accepted. Supersedes the single-chat choices in ADRs 0007 and 0011.

The user requested additional chats. Retain one coordinator service and normal-chat funding; do not create another model process, expose agent-route pickers in chat, or change Crew permissions.

Each visible conversation links to a unique coordinator session with its own bounded model context and planning-Crew pointer. New chat creates empty persisted records without inference. The first successful user turn supplies a deterministic title; users may rename it. Never seed a new chat from another chat's messages or the globally newest setup draft. Crew builder sessions remain independent.

Migration 0014 adds a nullable conversation reference and unique index to coordinator sessions, preserving the existing main conversation, earlier history, Crew and completed turns. SQLite uses additive ALTER TABLE with an inline nullable foreign key, not table recreation. Deleting a conversation cascades its messages and linked planning dialogue/turns, but not Crew. Refuse deletion while an unexpired work lease is held, and reject stale completions after expired-session deletion. Requests bind conversation ID to session ID before invoking the coordinator.

Use a compact black/gray history pane with New chat, selection, rename and confirmed deletion. Pending responses, errors and unsent composer text are keyed by chat ID. A response may finish while another chat is selected without moving selection. Clear private state on logout and fence late responses with the workspace epoch. Unfinished Crew drafts remain explicitly recoverable from the Crew page after their chat is deleted.

Test isolated prompts and questionnaires, persistence/migration preservation, exact idempotency, simultaneous chats, lease conflicts, deleted-session completion, title validation, account logout races and desktop layout. UI smoke tests create/remove only their own empty chats and incur no model charges.
