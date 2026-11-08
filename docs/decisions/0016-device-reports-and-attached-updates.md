# 0016: Deterministic device reports and attached desktop updates

Status: Accepted.

## Problem

Computer Crew collected system counters but never published a notification. A valid request to report C-drive free space every five minutes therefore produced only an internal checkpoint. Generic system-drive-used percentages did not meet the requested free-GB report. Reminder delivery also lived in the main WebView and opened a separate centered window.

## Decision

Use validated `DeviceMonitor` settings, persisted in existing configuration JSON and included in coordinator plans. Device checks are deterministic and never require inference. Storage mode reads only the selected drive's OS counters, reports free/total GiB (labelled GB in the UI) and percentage free, and never enumerates or alters files. Only a local drive letter or system is accepted. Existing explicit drive/every-check requests have a narrow compatibility parser that normalizes settings without granting access. Manual create/edit exposes the same settings.

Notification mode is alerts (new warning/recovery), each_check, or changes (changed displayed status). Alerts retain a low-space warning until free space exceeds the configured threshold by 1 GB. Each-check/changed reports supersede unread older status reports from the same Crew rather than accumulating stale popups. Reading requires device:read; final publication and delivery both check notifications:show. Failed reads stop for input with an explicit error report, not a fabricated metric.

The always-present status WebView owns the delivery watcher. Native reminder state and existing commands are reused, but presentation expands the docked icon rather than creating another window. Native resizing preserves/clamps its anchor and does not request keyboard focus. One pending message is displayed at a time. Dismiss acknowledges the durable event before clearing native state. Collapse retains an unread indicator. Check now and Stop target the originating Crew; the panel never invents or auto-executes cleanup actions. Previews remain labelled, and Read aloud uses Windows speech.

## Verification

Tests cover exact storage results, no model usage, every-check delivery, coalescing, warning hysteresis/recovery, revocation, errors, path restrictions, manual API lifecycle, and safe card controls. Isolated examples use real disk/health counters and durable daily reminders. A desktop check requests the user's already-running read-only C-drive check, verifies the actual attached message and collapse/reopen, and preserves its settings. Native edge tests include the enlarged message panel. Browser/model examples remain separately opt-in billed checks.
