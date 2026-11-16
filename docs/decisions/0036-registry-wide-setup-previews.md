# 0036 — Registry-wide non-executing setup previews

Status: accepted

## Context

The mandatory first-run gate rejected useful authorized Crew, including Gmail mark-read,
because its calendar/task preview allowlist did not cover other tools. Executing effects
to prove setup works would send messages, alter files or control the PC without a real task.

## Decision

Extend the existing private first-run adapter, ActionRequest, policy and review/activate
path. All typed registered tools default to non-executing previews except the explicit
read/navigation set. Intercept before provider tokens, execution, shell/UI operations and
temporary helpers. Preserve actual-run script binding and approval requirements.

Version the shared worker/action preview protocol so mixed running modules fail closed.
Recheck source/worker scope, current access, provider consent and known Google item IDs;
use non-creating path checks. Never fabricate IDs, subprocess output or changed browser state.
Legacy Computer cleanup uses its current deterministic configuration, without deletion.

Show validated proposals and unexercised conditional operations separately. Store complete,
bounded review data and bind activation to accounts, all policies, budgets and configuration.
No migration, new permission grant, model provider, orchestration engine or account reset.

## Consequences

Any supported action can participate in setup without a blanket dangerous-action rejection.
Passing checks is not proof of runtime effects or a whole objective. External credentials,
fresh inputs, runtime preconditions and consequential-action approvals still apply at execution.
Tests use intercepted providers/tools; they do not send real mail or operate the user's PC.
