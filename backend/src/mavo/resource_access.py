"""Explicit, persisted access to built-in resources. Collection is denied by default."""

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from mavo.database import CrewInstance, WorkspaceSetting


class DefaultResourceAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    weather: bool = False
    location: bool = False


def read_access(session):
    saved = session.get(WorkspaceSetting, "default-resource-access")
    return DefaultResourceAccess.model_validate(saved.value if saved else {})


def require_access(session, resource):
    session.expire_all()
    if not getattr(read_access(session), resource):
        raise HTTPException(403, f"Allow {resource} in Resources first.")


def save_access(session, payload, weather):
    old = read_access(session)
    if old.weather and not payload.weather:
        from mavo.crew_workers import stop_crew

        for crew in session.scalars(select(CrewInstance)).all():
            if "weather:read" in crew.permissions and crew.is_running:
                stop_crew(session, crew)
    saved = session.get(WorkspaceSetting, "default-resource-access")
    if saved:
        saved.value = payload.model_dump()
    else:
        session.add(WorkspaceSetting(key="default-resource-access", value=payload.model_dump()))
    if not payload.weather or (old.location and not payload.location):
        weather.cache.clear()
    location = session.get(WorkspaceSetting, "weather-location")
    if (
        location
        and not payload.location
        and (
            location.value.get("source") == "device"
            or location.value.get("name") == "Current location"
        )
    ):
        session.delete(location)
    device = session.get(WorkspaceSetting, "device-location")
    if device and not payload.location:
        session.delete(device)
    session.commit()
    return payload
