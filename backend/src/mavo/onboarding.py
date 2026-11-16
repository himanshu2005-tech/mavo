"""Account-bound setup progress. Completing setup never grants resource or action access."""

from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from mavo.database import ModelRoute, WorkspaceSetting, utcnow

STEPS = ("welcome", "providers", "resources", "complete")


class SetupAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["next", "back", "skip"]
    revision: int = Field(ge=0)


def account_key(session):
    owner = session.get(WorkspaceSetting, "workspace-owner")
    binding = owner.value if owner else {}
    if not all(isinstance(binding.get(key), str) and binding[key] for key in ("uid", "project_id")):
        raise HTTPException(401, "Sign in before setting up your workspace.")
    digest = sha256((binding["project_id"] + "\0" + binding["uid"]).encode()).hexdigest()
    return "onboarding-v1-" + digest


def initial():
    return {
        "version": 1,
        "step": "welcome",
        "revision": 0,
        "completed_at": None,
        "providers_skipped": False,
    }


def build_onboarding_api(get_session, credentials):
    router = APIRouter()

    @router.get("/onboarding")
    def status(session=Depends(get_session)):
        key = account_key(session)
        row = session.get(WorkspaceSetting, key)
        state = row.value if row else initial()
        return {**state, "account_key": key}

    @router.put("/onboarding")
    def advance(payload: SetupAction, session=Depends(get_session)):
        key = account_key(session)
        row = session.get(WorkspaceSetting, key)
        state = dict(row.value) if row else initial()
        if state["revision"] != payload.revision:
            raise HTTPException(409, "Setup changed in another window. Reload setup to continue.")
        index = STEPS.index(state["step"])
        if state["step"] == "complete":
            raise HTTPException(409, "Setup is already complete. Use Settings to make changes.")
        if payload.action == "back":
            if index == 0:
                raise HTTPException(400, "You are already at the welcome step.")
            target = STEPS[index - 1]
        else:
            if payload.action == "skip" and state["step"] != "providers":
                raise HTTPException(400, "Only provider setup can be explicitly skipped.")
            if state["step"] == "providers":
                if payload.action == "next":
                    configured = any(
                        route.provider == "ollama"
                        or bool(route.credential_id and credentials.get(route.credential_id))
                        for route in session.scalars(select(ModelRoute))
                    )
                    if not configured:
                        raise HTTPException(409, "Add a model route, or choose Set up later.")
                state["providers_skipped"] = payload.action == "skip"
            target = STEPS[index + 1]
        state.update(step=target, revision=state["revision"] + 1)
        if target == "complete":
            state["completed_at"] = utcnow().isoformat()
        try:
            if row:
                result = session.execute(
                    update(WorkspaceSetting)
                    .where(
                        WorkspaceSetting.key == key,
                        WorkspaceSetting.value["revision"].as_integer() == payload.revision,
                    )
                    .values(value=state)
                )
                if result.rowcount != 1:
                    raise HTTPException(409, "Setup changed. Reload setup to continue.")
            else:
                session.add(WorkspaceSetting(key=key, value=state))
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "Setup changed. Reload setup to continue.") from None
        return {**state, "account_key": key}

    return router
