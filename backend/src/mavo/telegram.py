"""Mavo on Telegram: the person's own bot, linked to their own chat.

Out: every desktop alert can go to that chat too (on the person's choice), a question with its
answers as buttons. In: "@Agent name message" talks to that agent, as its room does (same
sorting, same history, same permissions); a reply to a question answers it.

The bot token is kept encrypted with Mavo's other secrets and never shown or logged. Only the one
chat that linked itself with a one-time code is listened to: messages from anyone else who finds
the bot are ignored. Nothing here reaches Telegram except through the person's own token."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from mavo.database import CrewInstance, ReminderEvent, WorkspaceSetting, utcnow

logger = logging.getLogger(__name__)

TOKEN_KEY = "telegram-bot"
SETTING = "telegram"
API = "https://api.telegram.org"
TOKEN = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
KEEP_SENT = 400
CODE_MINUTES = 30
MAX_TRIES = 5


def fresh_code() -> dict:
    """Six digits, easy to type on a phone, good for 30 minutes."""
    return {
        "link_code": f"{secrets.randbelow(10**6):06d}",
        "code_until": (utcnow() + timedelta(minutes=CODE_MINUTES)).isoformat(),
        "tries": {},
    }


def code_alive(value: dict) -> bool:
    until = value.get("code_until")
    if not value.get("link_code") or not until:
        return False
    moment = datetime.fromisoformat(until)
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)) > utcnow()


class TelegramError(RuntimeError):
    """A plain reason, safe to show: never the token, never Telegram's raw reply."""


def plain(text: str) -> str:
    """A report (Mavo's LaTeX or light markdown) as Telegram HTML: bold kept, lists as bullets,
    everything else as text, escaped."""
    s = (text or "").replace("\r\n", "\n")
    s = re.sub(r"\\href\{([^}]*)\}\{([^}]*)\}", r"\2 (\1)", s)
    s = re.sub(r"\\(?:sub)*section\*?\{([^}]*)\}", "\u0001\\1\u0002\n", s)
    s = re.sub(r"\\(?:textbf|textit|emph|texttt)\{([^}]*)\}", "\u0001\\1\u0002", s)
    s = re.sub(r"[ \t]*\\(?:begin|end)\{[^}]*\}(?:\{[^}]*\})?[ \t]*\n?", "", s)
    s = re.sub(r"[ \t]*\\item\s*", "\n• ", s)
    s = re.sub(r"\\\\", "\n", s)
    s = re.sub(r"\\([$%&#_{}])", r"\1", s)
    s = re.sub(r"\*\*([^*\n]+)\*\*", "\u0001\\1\u0002", s)
    s = re.sub(r"^\s{0,3}#{1,4}\s*(.+)$", "\u0001\\1\u0002", s, flags=re.M)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return html.escape(s).replace("\u0001", "<b>").replace("\u0002", "</b>")[:3900]


class Telegram:
    def __init__(self, factory, credentials, runtime, transport=None):
        self.factory = factory
        self.credentials = credentials
        self.runtime = runtime
        self.transport = transport

    # -- saved state ----------------------------------------------------------------------------

    def state(self, session) -> dict:
        saved = session.get(WorkspaceSetting, SETTING)
        return dict(saved.value or {}) if saved else {}

    def save(self, session, value: dict) -> None:
        saved = session.get(WorkspaceSetting, SETTING)
        if saved:
            saved.value = value
        else:
            session.add(WorkspaceSetting(key=SETTING, value=value))
        session.commit()

    def status(self) -> dict:
        with self.factory() as session:
            value = self.state(session)
        bot = value.get("bot") or {}
        connected = bool(self.credentials.get(TOKEN_KEY)) and bool(bot)
        return {
            "connected": connected,
            "bot_name": bot.get("name"),
            "bot_username": bot.get("username"),
            "linked": connected and bool(value.get("chat_id")),
            "chat_name": value.get("chat_name"),
            "alerts": bool(value.get("alerts", True)),
            # The link that opens the bot with the one-time code (press Start in Telegram).
            "link_url": (
                f"https://t.me/{bot['username']}?start={value['link_code']}"
                if connected and not value.get("chat_id") and code_alive(value)
                else None
            ),
            # The same code to type on a phone, and until when it works.
            "link_code": (
                value["link_code"]
                if connected and not value.get("chat_id") and code_alive(value)
                else None
            ),
            "code_expired": connected and not value.get("chat_id") and not code_alive(value),
        }

    def new_code(self) -> dict:
        """A fresh one-time code (the last one expired, or was never seen)."""
        with self.factory() as session:
            value = self.state(session)
            if value.get("bot") and not value.get("chat_id"):
                value |= fresh_code()
                self.save(session, value)
        return self.status()

    # -- Telegram's API -------------------------------------------------------------------------

    async def call(self, method: str, timeout: float = 20, **params) -> dict:
        token = self.credentials.get(TOKEN_KEY)
        if not token:
            raise TelegramError("Connect your Telegram bot first.")
        return await self.call_with(token, method, timeout, **params)

    async def call_with(self, token: str, method: str, timeout: float = 20, **params) -> dict:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=timeout + 10) as client:
                reply = await client.post(f"{API}/bot{token}/{method}", json=params)
            data = reply.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError("Mavo could not reach Telegram. Check your connection.") from exc
        if not data.get("ok"):
            code = data.get("error_code")
            if code == 401:
                raise TelegramError("Telegram did not accept that bot token. Copy it again.")
            if code == 409:
                raise TelegramError("Another app is reading this bot's messages. Stop it first.")
            raise TelegramError("Telegram refused the request. Try again in a minute.")
        return data.get("result")

    async def send(self, text: str, **extra) -> dict | None:
        with self.factory() as session:
            chat = self.state(session).get("chat_id")
        if not chat:
            return None
        return await self.call(
            "sendMessage",
            chat_id=chat,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            **extra,
        )

    # -- connecting -----------------------------------------------------------------------------

    async def connect(self, token: str) -> dict:
        token = (token or "").strip()
        if not TOKEN.match(token):
            raise TelegramError(
                "That does not look like a bot token. It looks like 123456789:AAE... (from "
                "BotFather)."
            )
        me = await self.call_with(token, "getMe", 10)
        self.credentials.set(TOKEN_KEY, token)
        with self.factory() as session:
            self.save(
                session,
                {
                    "bot": {
                        "id": me.get("id"),
                        "name": me.get("first_name"),
                        "username": me.get("username"),
                    },
                    **fresh_code(),
                    "alerts": True,
                    "offset": 0,
                    "sent": [],
                    "waiting": [],
                    "questions": {},
                },
            )
        return self.status()

    def disconnect(self) -> None:
        self.credentials.delete(TOKEN_KEY)
        with self.factory() as session:
            saved = session.get(WorkspaceSetting, SETTING)
            if saved:
                session.delete(saved)
                session.commit()

    def set_alerts(self, on: bool) -> dict:
        with self.factory() as session:
            value = self.state(session)
            value["alerts"] = bool(on)
            self.save(session, value)
        return self.status()

    # -- one round: read what came in, send what is due --------------------------------------

    async def tick(self, wait: int = 20) -> None:
        with self.factory() as session:
            offset = self.state(session).get("offset", 0)
        updates = await self.call(
            "getUpdates", timeout=wait, offset=offset, allowed_updates=["message", "callback_query"]
        )
        for update in updates or []:
            with self.factory() as session:
                value = self.state(session)
                value["offset"] = max(value.get("offset", 0), update["update_id"] + 1)
                self.save(session, value)
            try:
                await self.handle(update)
            except TelegramError:
                raise
            except Exception:  # noqa: BLE001 - one bad message never stops the bridge
                logger.warning("Telegram message not handled", exc_info=True)
        await self.deliver()
        await self.answers()

    async def handle(self, update: dict) -> None:
        if query := update.get("callback_query"):
            await self.choice(query)
            return
        message = update.get("message") or {}
        chat = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        if not chat or not text:
            return
        with self.factory() as session:
            value = self.state(session)
        if not value.get("chat_id"):
            # Only the person holding the one-time code links their chat: sent with Start (the
            # link on the computer) or typed on the phone. A chat that keeps guessing is ignored.
            tries = dict(value.get("tries") or {})
            if tries.get(str(chat), 0) >= MAX_TRIES:
                return
            typed = re.sub(r"\D", "", text.removeprefix("/start"))
            given = text.removeprefix("/start").strip()
            alive = code_alive(value)
            if not alive or (given != value.get("link_code") and typed != value.get("link_code")):
                if given or typed:
                    tries[str(chat)] = tries.get(str(chat), 0) + 1
                    with self.factory() as session:
                        latest = self.state(session)
                        latest["tries"] = tries
                        self.save(session, latest)
                await self.call(
                    "sendMessage",
                    10,
                    chat_id=chat,
                    text=(
                        "That code has expired. In Mavo, press New code and send the new one."
                        if (given or typed) and not alive
                        else "That code is not right. Check the 6 digits in Mavo and send them."
                        if given or typed
                        else "To link this chat, send the 6-digit code shown in Mavo on your "
                        "computer (Resources, Telegram)."
                    ),
                )
                return
            # The right code, in time: this chat is the person's.
            who = message.get("from") or {}
            with self.factory() as session:
                value = self.state(session)
                value |= {
                    "chat_id": chat,
                    "chat_name": who.get("first_name") or who.get("username") or "you",
                    "linked_at": utcnow().isoformat(),
                    "link_code": None,
                    "tries": {},
                }
                self.save(session, value)
            await self.send(
                "Linked. Your Mavo alerts come here now.\n\nTo talk to an agent, start with "
                "its name: <b>@" + html.escape(self.example_name()) + "</b> your message.\n"
                "/agents lists them."
            )
            return
        if chat != value["chat_id"]:
            return  # anyone else who finds the bot is ignored
        reply_to = (message.get("reply_to_message") or {}).get("message_id")
        if reply_to and str(reply_to) in (value.get("questions") or {}):
            await self.answer_question(value["questions"][str(reply_to)], text)
            return
        if text.split()[0].lower() in {"/start", "/agents", "/help"}:
            await self.send(self.agents_text())
            return
        crew, rest = self.addressed(text)
        if crew is None:
            await self.send(
                "Which agent is this for? Start with its name, like <b>@"
                + html.escape(self.example_name())
                + "</b> your message.\n\n"
                + self.agents_text()
            )
            return
        if not rest:
            await self.send(f"What should I tell <b>{html.escape(crew.name)}</b>?")
            return
        await self.ask(crew, rest)

    # -- talking to an agent ----------------------------------------------------------------------

    def crews(self) -> list[CrewInstance]:
        with self.factory() as session:
            return list(
                session.scalars(
                    select(CrewInstance).where(
                        CrewInstance.hidden.is_(False), CrewInstance.status != "draft"
                    )
                )
            )

    def example_name(self) -> str:
        crews = self.crews()
        return crews[0].name if crews else "Agent name"

    def agents_text(self) -> str:
        crews = self.crews()
        if not crews:
            return "You have no agents yet. Make one in Mavo."
        return "Your agents:\n" + "\n".join(f"• @{html.escape(c.name)}" for c in crews)

    def addressed(self, text: str) -> tuple[CrewInstance | None, str]:
        """ "@Momentum Spark give me a quote", "@MomentumSpark …", "@momentum_spark …": the agent
        whose name the message starts with (the longest that fits), and the rest."""
        if not text.startswith("@"):
            return None, text
        body = text[1:]
        squash = lambda s: re.sub(r"[\s_-]+", "", s).casefold()  # noqa: E731
        best, rest = None, ""
        for crew in self.crews():
            name = crew.name.strip()
            for form in {name, name.replace(" ", ""), name.replace(" ", "_")}:
                if body.casefold().startswith(form.casefold()) and (
                    best is None or len(form) > len(best.name)
                ):
                    best, rest = crew, body[len(form) :]
        if best is None:
            head = body.split()[0] if body.split() else ""
            matches = [c for c in self.crews() if squash(c.name).startswith(squash(head))]
            if head and len(matches) == 1:
                best, rest = matches[0], body[len(head) :]
        return best, rest.strip(" ,:-\n")

    async def ask(self, crew: CrewInstance, text: str) -> None:
        from mavo.crew_ask import AskCrew

        ask = getattr(self.runtime, "ask_crew", None)
        if ask is None:
            await self.send("Mavo is starting. Try again in a moment.")
            return
        await self.call("sendChatAction", 10, chat_id=self.chat(), action="typing")
        try:
            item = await ask(crew.id, AskCrew(question=text[:1000]))
        except Exception as exc:  # noqa: BLE001 - said plainly, in the chat
            detail = getattr(exc, "detail", None) or "It could not answer that. Ask again."
            await self.send(f"<b>{html.escape(crew.name)}</b>\n{html.escape(str(detail))}")
            return
        head = f"<b>{html.escape(crew.name)}</b>\n"
        if item.get("answer"):
            await self.send(head + plain(item["answer"]))
        if item.get("kind") == "beyond" and item.get("prompt"):
            await self.send(
                "To get this done, ask Mavo in the app:\n" + html.escape(item["prompt"])
            )
        if item.get("pending"):
            if not item.get("answer"):
                await self.send(head + "Working on it…")
            with self.factory() as session:
                value = self.state(session)
                value["waiting"] = [*value.get("waiting", []), {"crew": crew.id, "id": item["id"]}]
                self.save(session, value)

    async def answers(self) -> None:
        """Answers that took a while (a fresh look at the inbox) follow when they are ready."""
        listing = getattr(self.runtime, "ask_items", None)
        with self.factory() as session:
            waiting = list(self.state(session).get("waiting") or [])
        if not waiting or listing is None:
            return
        still = []
        for wait in waiting:
            try:
                items = listing(wait["crew"])
            except Exception:  # noqa: BLE001 - its agent was removed
                continue
            item = next((i for i in items if i["id"] == wait["id"]), None)
            if item is None:
                continue
            if item.get("pending"):
                still.append(wait)
                continue
            crew = next((c for c in self.crews() if c.id == wait["crew"]), None)
            await self.send(
                f"<b>{html.escape(crew.name if crew else 'Agent')}</b>\n"
                + plain(item.get("answer") or "")
            )
        with self.factory() as session:
            value = self.state(session)
            value["waiting"] = still
            self.save(session, value)

    def chat(self):
        with self.factory() as session:
            return self.state(session).get("chat_id")

    # -- alerts ---------------------------------------------------------------------------------

    async def deliver(self) -> None:
        """Desktop alerts since the chat was linked, once each: every one when the person turned
        that on, else those of the agents they asked to send to Telegram."""
        with self.factory() as session:
            value = self.state(session)
            if not value.get("chat_id"):
                return
            everything = bool(value.get("alerts", True))
            chosen = {
                c.id
                for c in session.scalars(select(CrewInstance))
                if ((c.configuration_plan or {}).get("draft") or {}).get("requested_telegram")
            }
            if not everything and not chosen:
                return
            from mavo.reminders import create_due_reminders

            create_due_reminders(session)  # daily reminders fall due here as on the desktop
            since = datetime.fromisoformat(value["linked_at"])
            sent = set(value.get("sent") or [])
            due = [
                e
                for e in session.scalars(
                    select(ReminderEvent)
                    .where(ReminderEvent.scheduled_for <= utcnow())
                    .order_by(ReminderEvent.scheduled_for)
                )
                if e.id not in sent
                and (e.created_at.replace(tzinfo=e.created_at.tzinfo or UTC) >= since)
                and e.status != "cancelled"
                and (everything or e.crew_id in chosen)
            ][:10]
            choices = {}
            for event in due:
                if event.kind == "question":
                    crew = session.get(CrewInstance, event.crew_id)
                    asks = ((crew.observation or {}).get("desk_asks") or {}) if crew else {}
                    choices[event.id] = list((asks.get(event.id) or {}).get("choices") or [])
        for event in due:
            text = f"<b>{html.escape(event.crew_name)}</b>\n{plain(event.message)}"
            extra = {}
            if event.kind == "question":
                buttons = [
                    [{"text": c[:60], "callback_data": f"a:{event.id}:{n}"}]
                    for n, c in enumerate(choices.get(event.id, [])[:6])
                ]
                text += "\n\n<i>Reply to this message to answer in your own words.</i>"
                if buttons:
                    extra["reply_markup"] = {"inline_keyboard": buttons}
            sent_message = await self.send(text, **extra)
            with self.factory() as session:
                value = self.state(session)
                value["sent"] = [*value.get("sent", []), event.id][-KEEP_SENT:]
                if event.kind == "question" and sent_message:
                    questions = dict(value.get("questions") or {})
                    questions[str(sent_message.get("message_id"))] = event.id
                    value["questions"] = dict(list(questions.items())[-50:])
                self.save(session, value)

    async def choice(self, query: dict) -> None:
        data = str(query.get("data") or "")
        with self.factory() as session:
            chat = self.state(session).get("chat_id")
        if ((query.get("message") or {}).get("chat") or {}).get("id") != chat:
            return
        await self.call("answerCallbackQuery", 10, callback_query_id=query["id"])
        found = re.fullmatch(r"a:([0-9a-f-]{36}):(\d+)", data)
        if not found:
            return
        with self.factory() as session:
            event = session.get(ReminderEvent, found[1])
            crew = session.get(CrewInstance, event.crew_id) if event else None
            asks = ((crew.observation or {}).get("desk_asks") or {}) if crew else {}
            options = list((asks.get(found[1]) or {}).get("choices") or [])
        index = int(found[2])
        if index < len(options):
            await self.answer_question(found[1], options[index])

    async def answer_question(self, reminder_id: str, answer: str) -> None:
        from mavo.reminders import answer_question

        with self.factory() as session:
            problem = answer_question(session, reminder_id, answer)
        await self.send(
            "Already answered."
            if problem and problem[0] == 409
            else "That question is gone."
            if problem
            else f"Got it: {html.escape(answer)}. It carries on."
        )


async def keep_telegram(bridge: Telegram) -> None:
    """Runs while Mavo runs: reads the linked chat and sends what is due. Quiet when no bot is
    connected; backs off when Telegram cannot be reached."""
    pause = 2
    while True:
        try:
            if bridge.credentials.get("telegram-bot"):
                await bridge.tick()
                pause = 2
            else:
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise
        except TelegramError as exc:
            logger.info("Telegram: %s", exc)
            await asyncio.sleep(pause)
            pause = min(pause * 2, 120)
        except Exception:  # noqa: BLE001 - the bridge keeps going
            logger.warning("Telegram round failed", exc_info=True)
            await asyncio.sleep(pause)
            pause = min(pause * 2, 120)


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=200)


class AlertsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool


def build_telegram_api(bridge: Telegram):
    from fastapi import APIRouter, HTTPException

    router = APIRouter()

    @router.get("/telegram")
    def telegram_status():
        return bridge.status()

    @router.put("/telegram/token")
    async def telegram_connect(payload: TokenIn):
        try:
            return await bridge.connect(payload.token)
        except TelegramError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.put("/telegram/alerts")
    def telegram_alerts(payload: AlertsIn):
        return bridge.set_alerts(payload.on)

    @router.post("/telegram/code")
    def telegram_code():
        return bridge.new_code()

    @router.post("/telegram/test")
    async def telegram_test():
        try:
            sent = await bridge.send("Hello from Mavo. Alerts will look like this.")
        except TelegramError as exc:
            raise HTTPException(400, str(exc)) from None
        if not sent:
            raise HTTPException(409, "Open your bot in Telegram and press Start first.")
        return {"sent": True}

    @router.delete("/telegram", status_code=204)
    def telegram_disconnect():
        bridge.disconnect()

    return router
