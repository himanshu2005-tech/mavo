from fastapi import APIRouter, HTTPException

from mavo.google_auth import SCOPES, GoogleAuth, GoogleConnectionError
from mavo.google_permissions import catalog
from mavo.resources.google import GoogleResourcePool
from mavo.schemas import GoogleClientRequest, GoogleResource, GoogleSignInRequest


def build_google_api(auth: GoogleAuth, resources: GoogleResourcePool):
    router = APIRouter()

    @router.get("/auth/google")
    def google_status():
        return auth.status()

    @router.get("/google/connectors")
    def connectors():
        return catalog()

    @router.put("/auth/google/client")
    async def configure_client(payload: GoogleClientRequest):
        try:
            await auth.configure(
                payload.client_id,
                payload.client_secret.get_secret_value() if payload.client_secret else None,
            )
            return auth.status()
        except GoogleConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/auth/google/start")
    async def sign_in(payload: GoogleSignInRequest):
        try:
            return await auth.start(
                list(SCOPES) if payload.all_services else payload.resources, payload.capabilities
            )
        except GoogleConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/auth/google/cancel")
    async def cancel_sign_in():
        await auth.cancel()
        return auth.status()

    @router.post("/auth/google/check")
    async def check_connections():
        return await resources.check_connections()

    @router.delete("/auth/google", status_code=204)
    async def disconnect():
        await auth.disconnect()
        resources.clear()

    @router.post("/auth/google/revoke")
    async def revoke():
        result = await auth.revoke()
        resources.clear()
        return result

    @router.get("/resources/google/{resource}")
    async def read_resource(resource: GoogleResource):
        try:
            return await resources.read(resource)
        except GoogleConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/resources/google/gmail/messages/{message_id}")
    async def message_body(message_id: str):
        try:
            return await resources.message_body(message_id)
        except GoogleConnectionError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/resources/google/{resource}/items/{item_id}")
    async def item_detail(resource: GoogleResource, item_id: str):
        from urllib.parse import quote

        if resource not in {"calendar", "tasks"}:
            raise HTTPException(400, "Item editing is supported only for Calendar and Tasks")
        path = (
            "/calendars/primary/events/" if resource == "calendar" else "/lists/@default/tasks/"
        ) + quote(item_id, safe="")
        try:
            data = await resources.get(resource, path)
            fields = (
                ("id", "etag", "summary", "start", "end", "description")
                if resource == "calendar"
                else ("id", "etag", "title", "notes", "due")
            )
            if any(
                isinstance(data.get(key), str) and len(data[key]) > 8000
                for key in ("description", "notes")
            ):
                raise HTTPException(
                    409, "This item's text exceeds Mavo's edit limit. Edit it in Google."
                )
            return {key: data[key] for key in fields if key in data}
        except GoogleConnectionError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
