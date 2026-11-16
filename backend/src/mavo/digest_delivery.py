"""Deterministic eligibility for source-verified digests, not model-chosen delivery."""


def new_digest_evidence(agent, run_id, permissions=None):
    worker = agent.worker
    verification = agent.execution.get("verification", {})
    if (
        not worker
        or agent.status != "active"
        or agent.execution.get("task_id") != run_id
        or agent.definition.get("verification") != "source_evidence"
        or verification.get("step") != "evidence_checked"
        or verification.get("quiet")
        or worker.configuration_plan.get("first_run_source")
    ):
        return []
    # The parent Crew delivers its own verified outcome. Its specialists need
    # source access, not permission to independently publish desktop alerts.
    # Parent notifications permission is checked by the outbox authority.
    receipts = worker.observation.get("source_receipts", {})
    snapshots = receipts.get("snapshots", {}) if receipts.get("scope") == run_id else {}
    items = [
        item
        for key, snapshot in snapshots.items()
        if key.startswith("feeds:")
        and key + ":read" in worker.permissions
        and (permissions is None or key + ":read" in permissions)
        for item in snapshot.get("items", [])
    ]
    known = worker.observation.get("notified_source_ids", [])
    return list(
        dict.fromkeys(
            evidence["evidence_id"]
            for evidence in verification.get("evidence", [])
            if evidence.get("kind") == "source"
            and evidence.get("evidence_id")
            and evidence["evidence_id"] not in known
            and any(
                evidence.get("url")
                == (
                    item.get("url")
                    or (item.get("feed_url") if evidence.get("source_ref") else None)
                )
                and evidence.get("quote")
                and (
                    not evidence.get("source_ref")
                    or evidence["source_ref"] == item.get("source_ref")
                )
                and evidence["quote"]
                in "\n".join(value for value in item.values() if isinstance(value, str))
                for item in items
            )
        )
    )
