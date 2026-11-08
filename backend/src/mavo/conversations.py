"""Core chat storage and atomic lifecycle checks, separate from agent execution."""

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.orm import selectinload

from mavo.database import Conversation, CoordinatorSession, CrewInstance, utcnow
from mavo.schemas import ConversationView, CrewView


def conversation_for(session, thread):
    if not thread or not thread.conversation_id:
        return None
    return session.scalar(
        select(Conversation)
        .where(
            Conversation.id == thread.conversation_id,
        )
        .options(selectinload(Conversation.messages))
    )


def core_chat_view(session, thread):
    conversation = conversation_for(session, thread)
    if not conversation:
        raise HTTPException(404, "Chat not found")
    return {
        **ConversationView.model_validate(conversation).model_dump(mode="json"),
        "session_id": thread.id,
        "planning_crew_id": thread.crew_id,
        "planning_crew_ids": list(thread.crew_ids or []),
    }


def chat_drafts(session, thread):
    ids = (
        list(
            dict.fromkeys([*(thread.crew_ids or []), *([thread.crew_id] if thread.crew_id else [])])
        )
        if thread
        else []
    )
    crews = [session.get(CrewInstance, item) for item in ids]
    return [CrewView.model_validate(c) for c in crews if c and c.status == "draft"]


def claim_idle_chat(session, conversation_id):
    """Take SQLite's write lock before checking/deleting a chat; no read/write race."""
    claimed = session.execute(
        update(CoordinatorSession)
        .where(
            CoordinatorSession.conversation_id == conversation_id,
            or_(
                CoordinatorSession.lease_until.is_(None), CoordinatorSession.lease_until < utcnow()
            ),
        )
        .values(active_request=None, lease_until=None)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount == 0 and session.scalar(
        select(CoordinatorSession.id).where(
            CoordinatorSession.conversation_id == conversation_id,
        )
    ):
        raise HTTPException(409, "Wait for this chat's reply before deleting it.")
