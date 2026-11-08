from fastapi import HTTPException

from mavo.resources.google import EVENT_RESOURCES


def validate_trigger(trigger_type, resources):
    resource = EVENT_RESOURCES.get(trigger_type)
    if resource and resource not in resources:
        raise HTTPException(
            status_code=400,
            detail=f"This trigger requires an explicit per-Crew Google {resource} read permission",
        )


def google_grants(resources, capabilities=()):
    caps = set(capabilities) | {f"{name}:read" for name in resources}
    names = sorted({cap.split(":")[0] for cap in caps})
    return [f"google:{name}" for name in names], [f"google:{cap}" for cap in sorted(caps)]
