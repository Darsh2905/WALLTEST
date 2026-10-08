"""Run manager: one audit campaign at a time, events fanned out to Server-Sent-Events subscribers.
Every event comes from the engine, which read it from the database (access_event, trade_order, v_slot_score, ...)."""
from __future__ import annotations

import asyncio
import collections
import json
import time

from .engine import CampaignRunner, RunConfig


class Busy(Exception):
    pass


class RunManager:
    def __init__(self, db):
        self.db = db
        self.task: asyncio.Task | None = None
        self.runner: CampaignRunner | None = None
        self.events: collections.deque = collections.deque(maxlen=6000)
        self.subs: set[asyncio.Queue] = set()
        self.seq = 0
        self.state = "idle"                   # idle | running | finished | cancelled | error
        self.error: str | None = None
        self.campaign_id: int | None = None
        self._created = asyncio.Event()
        self.started_wall: float | None = None

    # -- events ------------------------------------------------------------------------------------------------------------
    def publish(self, e: dict) -> None:
        self.seq += 1
        e = {**e, "seq": self.seq, "server_time": time.time()}
        self.events.append(e)
        if e["type"] == "campaign_created":
            self.campaign_id = e["campaign_id"]
            self._created.set()
        for q in list(self.subs):
            try:
                q.put_nowait(e)
            except asyncio.QueueFull:
                self.subs.discard(q)          # a hopelessly slow client is dropped; it can reconnect and replay

    def subscribe(self, replay: bool = True) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=20000)
        if replay:
            for e in self.events:
                q.put_nowait(e)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subs.discard(q)

    @staticmethod
    def encode(e: dict) -> str:
        return json.dumps(e, default=str)

    # -- lifecycle -----------------------------------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self, cfg: RunConfig) -> dict:
        if self.running:
            raise Busy("a campaign is already running")
        self.events.clear()
        self.state, self.error, self.campaign_id = "running", None, None
        self._created = asyncio.Event()
        self.runner = CampaignRunner(self.db, cfg, self.publish)
        self.started_wall = time.time()
        self.task = asyncio.create_task(self._run())
        waiter = asyncio.create_task(self._created.wait())
        done, _ = await asyncio.wait({self.task, waiter}, return_when=asyncio.FIRST_COMPLETED, timeout=20)
        waiter.cancel()
        if self.task in done and self.error:
            raise RuntimeError(self.error)
        return {"campaign_id": self.campaign_id, "state": self.state}

    async def _run(self) -> None:
        try:
            await self.runner.run()
            self.state = "finished"
            self.publish({"type": "run_finished", "campaign_id": self.campaign_id})
        except asyncio.CancelledError:
            self.state = "cancelled"
            self.publish({"type": "run_cancelled", "campaign_id": self.campaign_id})
            raise
        except Exception as ex:  # noqa: BLE001
            self.state, self.error = "error", f"{type(ex).__name__}: {ex}"
            self.publish({"type": "run_error", "message": self.error, "campaign_id": self.campaign_id})
            self._created.set()

    async def cancel(self) -> bool:
        if not self.running:
            return False
        self.task.cancel()
        try:
            await self.task
        except BaseException:   # noqa: BLE001
            pass
        return True

    def status(self) -> dict:
        return {"state": self.state, "campaign_id": self.campaign_id, "error": self.error, "running": self.running,
                "last_seq": self.seq, "slots_done": getattr(self.runner, "slots_done", 0) if self.runner else 0,
                "planned": self.runner.cfg.planned_slots if self.runner else 0}
