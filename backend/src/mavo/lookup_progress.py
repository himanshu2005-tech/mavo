"""Bounded, assignment-scoped lookup receipts. Retrieved content is never authority."""

import hashlib
import json


def lookup_key(request):
    normalized = {
        k: " ".join(v.split()).casefold() if isinstance(v, str) else v
        for k, v in request.items()
        if v is not None
    }
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()


def scope_for(crew, observation=None):
    observation = crew.observation if observation is None else observation
    return str(
        crew.configuration_plan.get("task_run_id")
        or (str(crew.runtime_generation) + ":" + observation.get("review_started_at", "manual"))
    )


def current_receipts(crew, observation=None):
    scope = scope_for(crew, observation)
    saved = (observation if observation is not None else crew.observation).get(
        "lookup_progress", {}
    )
    return scope, list(saved.get("receipts", [])) if saved.get("scope") == scope else []


def remember(runtime, crew, scope, receipts):
    from mavo.database import CrewInstance, utcnow

    # Bounded durable evidence, committed before asking a model to interpret it.
    kept = receipts[-24:]
    while len(kept) > 1 and len(json.dumps(kept, default=str)) > 64000:
        kept = kept[1:]
    with runtime.factory() as session:
        row = session.get(CrewInstance, crew.id)
        if not row or not row.is_running or row.runtime_generation != crew.runtime_generation:
            raise RuntimeError("The lookup assignment is no longer active")
        row.observation = {
            **row.observation,
            "lookup_progress": {"scope": scope, "receipts": kept, "at": utcnow().isoformat()},
        }
        session.commit()
    return kept


def partial_summary(receipts, reason):
    ok = [r["response"] for r in receipts if r["response"].get("status") == "ok"]
    errors = list(
        dict.fromkeys(
            str(
                (r["response"].get("result") or {}).get("error")
                or r["response"].get("error")
                or "Source unavailable"
            )
            for r in receipts
            if r["response"].get("status") != "ok"
        )
    )
    message = reason
    if errors:
        message += " Source errors: " + "; ".join(errors)[:600]
    if ok:
        message += "\nPartial source results (not a verified final answer):\n" + "\n".join(
            json.dumps(r, ensure_ascii=False, default=str)[:1600] for r in ok[:3]
        )
    return message
