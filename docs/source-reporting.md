# Reference-bound resource reports

Mavo keeps source metadata in application-owned receipts. An LLM selects records;
it is not responsible for retyping their original links or proving that a connected
RSS publisher was fetched. Transport success establishes provenance, not the truth
of a publisher's reporting or the correctness of a generated interpretation.

## Runtime contract

1. Authorized connector snapshots are copied before use. Their item lists receive
   deterministic `source_ref` identifiers derived from resource key and identity.
   Provider/content-supplied identifiers with that field name are overwritten.
   Shared caches are never modified. Oversized records are skipped without starving
   smaller records; malformed and unavailable data remain explicit, not empty success.
2. A worker can return `source_items: [{"source_ref": "src_…"}]`. This references only
   records supplied in the current receipt. IDs do not authorize account access,
   tools, delegation or spending. Workers without reference-bearing inputs are not
   offered this field in their generation schema. Existing citation replies remain
   accepted for compatibility.
3. Feed digests resolve selected IDs to original headlines, publisher labels and
   links. They do not need another article fetch or a model-reconstructed quotation.
   The delivered report is built from resolved items, not arbitrary model prose.
   It is a headline digest, not a claim to have read full article contents.
   Headline/GUID-only feed entries remain readable: when no article URL exists,
   the report explicitly links to the configured **Publisher feed** instead of
   inventing an article address. Different entries sharing that endpoint remain
   distinct, and the endpoint is never recorded as a seen article URL.
4. Invalid selections/citations alongside valid stories produce an explicitly
   partial digest. Unsupported stories and their notification text are excluded.
   If nothing is supported despite available data, the existing single budgeted
   repair applies. If a feed-only non-review worker still selects nothing, Mavo
   falls back to **fetched headlines only**, labelled as unavailable AI selection,
   not ranked or analyzed results. Selection is deterministic, round-robin across
   available feeds, bounded to eight stories and the existing delivery size limit.
   Model-written prose/notifications are discarded. This requires no third call.
   Other resources, tool actions and reviewer decisions never use this fallback.
5. The final verifier rechecks assignment scope and current resource grants. It
   matches connector quotations/links within individual records, not across the
   entire snapshot. Browser evidence also requires a current browser-read grant
   and an assignment-fresh completed read. Lookup evidence requires current public
   lookup access. Explicitly stale, disconnected or unavailable snapshots cannot
   prove a current result. Referenced connector records use the same verifier.
6. Only reported feed IDs/URLs become seen when the parent cycle commits success.
   Failed downstream work, omitted or size-deferred stories remain eligible.
   Same-URL stories under different feed IDs do not repeatedly alert. Feed evidence
   delivery IDs are stable by original URL rather than model phrasing.
7. Parent-owned notification consent, generation fences, durable outbox insertion
   and deduplication remain in force. Complete bounded reports are retained up to
   12,000 characters; the old 1,800-character notification cut-off no longer hides
   later selected stories. Headline reports themselves are bounded so selected
   items beyond the delivery budget remain available instead of being marked seen.
   Legacy standalone workers use their existing permission-checked outbox when
   there is no parent task; they follow the same supported-record-only rule.

Google account/OAuth consent, weather/device adapters and action outcome verification
retain their existing resource-specific contracts. This change does not fabricate
URL citations for calendar writes, battery metrics or other non-document results.
References are available on fetched connector records; they are not a replacement
for provider authorization or action-level execution policies.

## Compatibility and limitations

There is no database migration, account reset or rewrite of existing Crew. Additional
worker fields and receipt metadata are stored in existing JSON columns. Old runs and
reports remain history; an old failure clears from current status after a successful
new run. Restart the development runtime to load the updated Python modules.

Every possible external outage/model response cannot be guaranteed away. Authentication
failure, unsupported tools, malformed JSON after bounded repair and lack of valid
source results still stop work safely. RSS is limited to publisher-supplied metadata
and its rolling item window. Headline selection does not prove factual completeness,
job eligibility or an entire multi-agent goal.

The failed live run on 2026-11-10 had 43 usable input items from six feeds but zero
model selections in both attempts. The old item-ID fix still depended on model
cooperation. The headline fallback removes that dependency for this failure shape;
it does not claim that model analysis succeeded. Receipt verification rechecks the
current parent/agent permission intersection after inference, including revocation.

## Verification

- `test_feed_fallback.py` and `test_news_checkpoints.py`: repeated empty/unknown model
  selections, honest headline-only delivery, unavailable sources, per-feed fairness,
  parent revocation during the repair, quiet recurrence and exactly-once alerts.
- `test_source_references.py`: typed and legacy partial reports; unknown references;
  cross-resource/assignment isolation; revocation; malformed/stale data; short Unicode
  headlines; duplicate identities/URLs; long links; size deferral; browser-read freshness;
  real worker → TaskRunner → durable notification flow with fixture providers.
- Existing news, source-receipt, first-run, digest-delivery, worker, connector, Google,
  lookup, device, weather and orchestration suites cover their respective boundaries.
- The optional `scripts/check-news-setup.py` works in a disposable workspace. Simulation
  uses no credentials. Live mode requires explicit opt-in and caps actual inference HTTP
  requests. On 2026-11-10, one approved AIRouter request using the saved Qwen route passed
  first-run verification: six valid selections from 35 public-feed input records, with
  no unknown references. The real Crew/accounts were not changed. This is not live
  verification of Google, GitHub, weather or every possible provider/model.
