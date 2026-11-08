# ADR 0005: Bounded text sessions and deterministic daily reminders

## Status

Accepted.

## Decision

Expose useful manual text assistance for every specialty before building external integrations. Each user-started session is exactly one shared Model Router call, checked against Crew status, permissions, and route. Save the run before inference, serialize per-Crew execution with a database constraint, and record success, failure, or interruption explicitly. Never replay an interrupted paid request automatically. Keep prompts isolated to one Crew and the user's supplied input.

Implement daily Schedule Crew reminders as a narrow deterministic feature. Persist once-per-day events, deliver only current-day reminders for enabled Crew, show one desktop overlay at a time, and persist dismissal. Mark previews explicitly. Speech uses local WebView2 voices. Timing requires a running, awake desktop; there is no service, tray runtime, or hosted scheduler yet.

Create WebView2 windows from an asynchronous Tauri command. Synchronous window creation can deadlock on Windows, leaving a blank overlay; this is documented in the [Tauri window builder reference](https://docs.rs/tauri/latest/tauri/webview/struct.WebviewWindowBuilder.html). Desktop verification must inspect rendered overlay content and dismissal, not just the existence of a native window.

## Consequences

Users can test actual model routes and specialists without suggesting nonexistent device/account access. Activity has real outcomes to show. The general Signals engine, durable leased job queue, tools, approvals, and Goals remain separate roadmap work. A manual text-session success does not verify any future external integration.
