# 0027 — Restore the original handoff map

Status: accepted, 2026-11-08

The user requested the map as it existed when Codex took over from Claude, not further redesign.
The original AgentGraph, radial geometry, map CSS and map contracts were reconstructed from
the recorded source patches, excluding failed patches. This restores the original centered
radial arrangement, canvas fit, node sizing, visible connections, hover behavior and animations.
The board and screen-space packing implementations are removed. Density improvements made
after the handoff are intentionally reverted; their verification claims no longer apply.

The rollback is presentation-only. Agent records, accounts, credentials and grants are not
modified. The pre-login startup fix, route connection tests and explicit opt-in cloud fallback
remain. Denied actions and stopped agents must still not produce activity-preview dots.

Final follow-up: the user requested the original style with only slightly larger text.
Independent node enlargement and counter-scaling are removed. Agent names increase from
12.5px to 14px, resource/model labels from 11.5px to 13px, and secondary text by 1px.
Original padding, icons, radial positions, camera behavior and detail levels remain intact.

Category follow-up: resources now use pills/circular icons, actions squared outlines/icons,
and model routes a double outline with CPU/cloud icons. Small category captions and a visible,
wrapping legend provide redundant cues. The original radial positions, font increase and
camera behavior stay unchanged; category styling does not grant access or imply execution.

Grouped-connections follow-up: the user requested a cleaner boxed representation rather than
many similarly styled floating connection nodes. Home now has one screen-space Connections
box with three counted sections: model routes, resources and actions. Agents retain their
radial arrangement. Camera framing reserves a lane for the box; it does not convert the map
into the rejected workflow board. Individual rows remain selectable graph endpoints.

Row anchors are measured relative to the graph canvas and converted into world coordinates.
Connections follow scroll, resize and camera movement. Scrolled-out entries attach to the
box boundary rather than drawing to invisible content. Dock scrolling does not zoom the map.
No Crew, permissions, accounts, routes or external actions are modified by grouping.

Verification: frontend unit tests, TypeScript/production build, isolated map browser checks
in both themes, and login/startup checks. Browser API changes use fixtures, not real accounts.
