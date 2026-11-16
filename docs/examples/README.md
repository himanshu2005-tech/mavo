# Practical Crew examples

[practical-crew-catalog.json](practical-crew-catalog.json) contains 100 deliberately scoped
examples across the 15 Crew templates. They are alternatives to choose from, not a
recommendation to run 100 agents simultaneously.

Each entry defines a goal, required inputs/access, specific work, and an expected output.
Recurring examples include an editable review interval. Three simple reminder examples
use local daily triggers, including multiple meal times in one Crew.

These configurations are stopped initially. The `needs` field describes setup requirements;
it does **not** grant permissions or claim an integration is connected. External automation
requires the corresponding working adapter/tool, a connected account where applicable,
individual resource/action grants, and any required browser sign-in. Other examples work
on information supplied directly by the user. Missing data must lead to a setup question,
not invented observations or successful actions.

`connection-requirements.json` supplies optional map suggestions for these labeled examples.
The frontend shows ungranted suggestions as dashed links. This is display metadata only:
it never authorizes a resource, connects an account, or changes the worker's tools. A shared
connection can have both authorized agents and agents still waiting for access.

Before using one, review its inputs, timing, notification preference, model route, and
execution budget. The example times are local-time defaults to edit, not recommendations
based on the user's actual timetable. Daily Schedule reminders use the deterministic local
reminder path; model-backed workflows consume the selected route's credits when run.

The common execution policy requires deduplication, explicit failure handling, scoped data
access, and honest evidence. Describing an intended capability does not implement a new
connector, guarantee prompt compliance, or replace Mavo's code-enforced permissions.
