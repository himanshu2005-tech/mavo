# Scoped actions and desktop presence

Status: accepted. Extends the initial human approval policy in ADR 0009.

User-owned settings choose a policy for each operation and assistant: Ask every time, Ask when needed, or Full freedom. The model cannot change these settings. Policy revisions fence pending requests; all modes still enforce OAuth grants, exact Crew permissions, configured scope, session ownership, pause state, schemas, and atomic claims.

Ask when needed uses a deterministic risk catalog. Sends, deletions, browser clicks/fills, and Windows input require review. Routine reads, task/event creation and updates, archive/read changes, and window focus/minimize can run automatically when enabled. Full freedom permits execution inside saved scope; automatic mail sending requires explicit recipients. Audit records distinguish human approval from saved-policy execution. Interrupted or uncertain actions are never automatically retried.

Terminal runs a fixed catalog of diagnostics and Git inspection with no shell, arbitrary arguments, installs, or downloaded scripts. It bounds output/time, reduces the environment, disables Git hooks/fsmonitor, and enforces approved folders. General-purpose execution awaits an isolated runner.

Browser uses installed Edge with a separate profile per assistant and a reduced child-process environment. Authorized HTTPS hosts resolve to public addresses pinned for the session; requests are checked again. Unsupported hosts, downloads, WebSockets, popups, password/file inputs, and model-supplied JavaScript are unavailable. Bounded DOM text/selectors are untrusted data. Compatibility is limited by these boundaries. Browser access is serialized and owned by one assistant at a time.

Windows tools enumerate approved app names. Focus/minimize/input recheck process identity and creation time to fence reused handles. Input requires the expected foreground window. Windows may refuse focus or elevated input. This is not general screen understanding or arbitrary mouse automation.

Automatic followups use the existing queue and daily limits, bounded by the Crew session model-call limit. Core can propose typed actions for direct requests; the Crew builder uses a proposal-only API flag and cannot execute actions. Retrieved results cannot grant access.

The tray icon and draggable status button show runtime presence and actual work/review counts. Closing the main window hides it; Quit ends the runtime after closing owned tools. A Windows kill-on-close Job Object owns the sidecar and its child processes, including the Python launcher and browser driver, so host crashes or development rebuilds cannot leave the managed tree running. Pause stops Crew, rejects pending requests, and closes desktop sessions. Sign-out also stops work and closes tools. Work still requires an awake Windows session.
