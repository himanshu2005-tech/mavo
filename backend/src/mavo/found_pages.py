"""Where an agent found its answer, remembered by its job rather than by the crew that ran it.

A setup test runs on a private copy of the crew, which is deleted afterwards: the credits page it
found (airouter.in/settings/credits) went with it, and the real crew started from the front page
again, never got past it, and failed. Keyed by the job's own words, the page found by the test is
there for the crew's first real run, and for every run after."""

import hashlib

from mavo.database import WorkspaceSetting, utcnow


def found_key(job: str) -> str:
    words = " ".join(str(job or "").casefold().split())
    return "found_at:" + hashlib.sha256(words.encode()).hexdigest()[:40]


def remember_found(session, job: str, url: str, quote: str) -> dict:
    spot = {"url": url, "quote": quote[:200], "at": utcnow().isoformat()}
    if job.strip():
        row = session.get(WorkspaceSetting, found_key(job))
        if row is None:
            session.add(WorkspaceSetting(key=found_key(job), value=spot))
        else:
            row.value = spot
    return spot


def recall_found(session, job: str) -> dict | None:
    row = session.get(WorkspaceSetting, found_key(job)) if job.strip() else None
    spot = dict(row.value) if row and isinstance(row.value, dict) else None
    return spot if spot and str(spot.get("url") or "").startswith("https://") else None


def not_found_words(session, worker_id: str, since) -> str:
    """Why a run's answer could not be shown, in plain words: the pages it read, and what to do.
    ("No source evidence matched an authorized source read in this assignment." said nothing.)"""
    from sqlalchemy import select

    from mavo.browse_cards import page_of
    from mavo.database import ActionRequest

    urls = session.scalars(
        select(ActionRequest.result)
        .where(
            ActionRequest.crew_id == worker_id,
            ActionRequest.created_at >= since,
            ActionRequest.operation.in_(["browser.open", "browser.read", "browser.navigate"]),
            ActionRequest.status == "completed",
        )
        .order_by(ActionRequest.created_at)
    ).all()
    pages = list(dict.fromkeys(page_of(r.get("url")) for r in urls if isinstance(r, dict)))
    pages = [p for p in pages if p]
    if not pages:
        return "It could not read the site this time. It tries again at its next check."
    return (
        f"It could not find the answer on the pages it read ({', '.join(pages[:3])}). It tries "
        "again at its next check; to help it, tell it which page has the answer."
    )


def jobs_of(session, crew) -> list[str]:
    """The jobs a found page is remembered under, most lasting first: the crew's own (the same
    for its setup test's copy, the crew itself, and each one-off helper a run starts), then the
    agent's own words. A helper's words differ each run, so its own key alone never matched."""
    from mavo.database import CrewInstance, TaskRun

    jobs = []
    run_id = ((crew.configuration_plan or {}).get("task_run_id")) if crew else None
    run = session.get(TaskRun, run_id) if run_id else None
    owner = session.get(CrewInstance, run.crew_id) if run is not None and run.crew_id else None
    for job in (owner.responsibilities if owner else None, crew.responsibilities if crew else None):
        if job and job.strip() and job not in jobs:
            jobs.append(job)
    return jobs


def recall_for(session, crew) -> dict | None:
    for job in jobs_of(session, crew):
        spot = recall_found(session, job)
        if spot:
            return spot
    return None
