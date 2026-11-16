"""Development-only Core lifecycle. Production desktops use a hosted publisher.

Never makes inference requests, handles credentials, or stops an adopted service.
Only a debug Tauri host explicitly opts in. Existing Crew execution is independent.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil

logger = logging.getLogger("mavo.dev_core")
BACKOFF = (1, 2, 5, 15)


def manages_url(url, enabled):
    if not enabled or not url:
        return False
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == 8741
            and parsed.path in {"", "/"}
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
        )
    except ValueError:
        return False


class DevCore:
    def __init__(
        self,
        url,
        enabled=False,
        *,
        root=None,
        probe=None,
        spawn=None,
        occupied=None,
        stop=None,
        clock=time.monotonic,
    ):
        self.enabled = manages_url(url, enabled)
        self.root = root or Path(__file__).resolve().parents[3]
        self.probe = probe or self._probe
        self.spawn = spawn or self._spawn
        self.occupied = occupied or self._occupied
        self.stop_child = stop or self._stop
        self.clock = clock
        self.child = None
        self.state = "starting" if self.enabled else "external"
        self.attempts = 0
        self.started = 0.0
        self.unhealthy_since = None
        self.next_retry = 0.0
        self.wake = asyncio.Event()
        self.task = None
        self.log_path = None

    async def _probe(self):
        try:
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                response = await client.get("http://127.0.0.1:8741/health")
                return response.status_code == 200 and response.json().get("service") == "mavo-core"
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    def _occupied(self):
        with socket.socket() as connection:
            connection.settimeout(0.2)
            return connection.connect_ex(("127.0.0.1", 8741)) == 0

    def _spawn(self):
        logs = self.root / ".build" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        self.log_path = logs / f"core-managed-{stamp}.log"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with self.log_path.open("ab") as output:
            return subprocess.Popen(
                [str(Path(sys.executable).resolve()), "-m", "mavo.publisher_gateway"],
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                creationflags=flags,
            )

    def _stop(self, child):
        # Windows venv Python has a launcher/base-Python tree. Stop only the
        # descendant processes we started, never an unrelated port owner.
        if child.poll() is not None:
            child.wait(timeout=5)
            return
        try:
            process = psutil.Process(child.pid)
            for descendant in reversed(process.children(recursive=True)):
                try:
                    descendant.terminate()
                except psutil.NoSuchProcess:
                    pass
            process.terminate()
        except psutil.NoSuchProcess:
            pass
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

    def retry(self):
        if self.enabled:
            self.attempts = 0
            self.next_retry = 0
            self.wake.set()

    async def tick(self):
        if not self.enabled:
            return
        now = self.clock()
        if self.child and self.child.poll() is not None:
            logger.warning("Development Core exited; inference was not retried")
            self.child = None
            self.state = "recovering"
            self.next_retry = now + BACKOFF[min(max(self.attempts - 1, 0), len(BACKOFF) - 1)]
        if await self.probe():
            self.state = "ready"
            self.unhealthy_since = None
            if now - self.started >= 30:
                self.attempts = 0
            return
        if self.child:
            if self.unhealthy_since is None:
                self.unhealthy_since = now
            # A single transient health timeout must not kill a live request.
            # Allow boot time, then require sustained unresponsiveness before recovery.
            if now - self.started < 25 or now - self.unhealthy_since < 10:
                self.state = "starting" if now - self.started < 25 else "recovering"
                return
            await asyncio.to_thread(self.stop_child, self.child)
            self.child = None
            self.next_retry = now + BACKOFF[min(max(self.attempts - 1, 0), len(BACKOFF) - 1)]
        if self.occupied():
            self.state = "port_conflict"
            return  # Do not kill or replace an unknown/unresponsive service.
        if self.attempts >= len(BACKOFF):
            self.state = "failed"
            return
        if now < self.next_retry:
            self.state = "recovering"
            return
        self.attempts += 1
        self.started = now
        self.unhealthy_since = now
        self.state = "starting"
        try:
            self.child = self.spawn()
            logger.info("Started development Core; attempt %s", self.attempts)
        except OSError:
            logger.exception("Could not start development Core")
            self.state = "failed"
            self.next_retry = now + BACKOFF[self.attempts - 1]

    async def run(self):
        while True:
            self.wake.clear()
            try:
                await self.tick()
            except Exception:
                logger.exception("Development Core supervision failed")
                self.state = "failed"
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=2)
            except TimeoutError:
                pass

    def start(self):
        if self.enabled and self.task is None:
            self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.child:
            await asyncio.to_thread(self.stop_child, self.child)
            self.child = None
