# 0012: Compact resources, explicit location access, and native desktop anchoring

Status: Accepted

Resources exposes two groups: Default resources (Weather and Location) and Google Workspace. One Connect all control requests the supported Gmail, Calendar, and Tasks capabilities; actual returned consent and per-Crew grants still govern access. This does not imply other Google products are implemented.

Built-in collection is denied by default using persisted independent access flags. Location can operate without Weather. Enabling Location and reopening Resources with permission automatically requests device geolocation, subject to Windows permission. Device coordinates are rounded and revocation deletes them; late callbacks cannot restore them. Manual city selection remains available with Weather permission. There is no silent IP fallback.

Normal-chat branding is MAVO CORE; provider/model controls remain specific to user-funded agent routes. A compact Settings Logout supersedes the earlier no-logout UI choice in ADR 0011. It clears private UI state and stops authenticated work without silently disconnecting Google resources.

The native status window is created hidden, resized after creation, and docked using physical dimensions against the monitor work area. Moves, scaling, and expansion are clamped to that area; collapsing restores the orb anchor. This avoids taskbar overlap, DPI drift, centered startup flashes, and off-screen panels.
