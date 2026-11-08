# ADR 0022: bounded Google extensions and curated public feeds

Status: accepted

## Decision

Extend the existing Google PKCE connection and publisher token broker with independent `drive:read` (`drive.metadata.readonly`) and `contacts:read` (`contacts.readonly`) capabilities. Do not request broad file content or contact write scopes. Existing accounts/Crew gain no access without consent and saved per-Crew grants.

Drive lists up to 100 recent files with metadata only; Contacts lists up to 100 saved contacts with names/emails/phones only. Shared resource caches are five/15 minutes. Workers receive at most ten records per Google resource and use scheduled reviews, not fabricated change events. A persistent SQLite counter caps actual HTTP reads/probes at 200 per resource/device/UTC day. Cache reads recheck authorization, account changes fence in-flight collection, and Crew generation/permissions are rechecked before model input.

This local cap is not a publisher-project spending limit. Keep billing disabled for strict no-charge testing. Document Google's changing Drive overage rules and public restricted-scope verification requirements. Tokens are DPAPI-protected; SQLite metadata snapshots are not encrypted by default. Cloud model routes may receive authorized data.

Add keyless public-feed resources for fixed arXiv AI and BBC Technology endpoints, each with its own grant. No arbitrary URLs, redirects, XML DTD/entities or executable plugin loading. Limit XML to 2 MB, 15 seconds, 20 metadata entries and a 15-minute cache. The 2 MB ceiling accommodates the measured 645 KB arXiv feed without exposing article bodies to models. Revocation removes exact feed grants, fences/stops workers and clears their checkpoints; other providers remain available. Historical work receipts can retain public titles.

## Alternatives and limitations

Selected-file Picker authorization and Docs/Sheets content tools remain future work; service-level Drive metadata is not folder-level isolation. Arbitrary RSS URLs need additional public-DNS pinning and destination controls. Notion and Telegram remain documentation-only candidates. No third-party account, subscription or paid model request is created by enabling a feed.
