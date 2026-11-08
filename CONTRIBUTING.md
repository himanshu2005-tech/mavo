# Contributing to Mavo

Mavo is an actively developed Windows desktop portfolio project. Keep changes focused,
testable, and honest about the capabilities they introduce.

## Before you start

- Read [Development](DEVELOPMENT.md), [Architecture](ARCHITECTURE.md), and [Security](SECURITY.md).
- Open an issue before a large feature, new provider, or architectural change.
- Use the documented interface preview if you only need to work on presentation.
- Never use another person's accounts or credentials for testing.

## Development workflow

1. Create a focused branch from `main`.
2. Reproduce the issue and inspect existing behavior before editing.
3. Add tests for the change, including denied access and failure cases where relevant.
4. Run `npm run check` and the affected backend tests; use `./scripts/check.ps1` for a broader Windows check.
5. Document user-visible changes and any new configuration or security implications.
6. Submit a pull request explaining the change, verification, and remaining limitations.

## Engineering expectations

- Keep the initial backend a modular monolith.
- Validate model output before it changes application state.
- Enforce permissions in application code, not just prompts.
- Distinguish proposed access, execution attempts, and verified results in the UI.
- Keep autonomous work bounded, restart-safe, and stoppable.
- Prefer deterministic logic over model calls for simple conditions.
- Preserve reduced-motion support and readable permission/error states.
- Use reviewed resource adapters and typed action contracts; do not execute arbitrary plugin code.
- Add an architectural decision record for material design changes.

## Safe tests and screenshots

Use isolated accounts, temporary databases, mock transports, or the existing preview fixtures.
Never silently trigger a paid model call, send an email, modify a real account, or run a
destructive tool in a regression test. Label sample-data screenshots clearly.

Do not commit `.env` files, private configuration, API keys, service-account credentials,
refresh tokens, databases, logs, virtual environments, or build outputs. Include sanitized
examples rather than real credentials.

## Reporting problems

For ordinary bugs, include the Windows/app version, reproduction steps, expected behavior,
and redacted diagnostics. Follow [SECURITY.md](SECURITY.md) for security-sensitive reports;
do not publish credentials or private account information in an issue.

## Licensing

The project does not yet specify a project-wide software license. Discuss licensing with
the repository owner before a substantial contribution or redistribution.
