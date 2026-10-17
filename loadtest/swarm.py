"""A class of simulated students taking one assessment at the same time.

Each student is a Streamlit browser session without the browser: one websocket
speaking the same protobuf messages the frontend sends -- including the timer
reruns of `run_every` fragments, which is most of what an open paper costs the
server -- so a hundred of them fit on a laptop that could not run a hundred
Chromium tabs. Browser rendering time is not measured; everything the server
does is.

    python loadtest/swarm.py --base https://quiz.batchllm-workspace.info \
        --accounts accounts.json --quiz quiz.json --users 35 --out results.json

Latency is measured the way a student would notice it: from the message that a
click sends to the end of the script run that shows its effect on screen.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import websockets
from streamlit.proto.Alert_pb2 import Alert
from streamlit.proto.BackMsg_pb2 import BackMsg
from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from streamlit.proto.WidgetStates_pb2 import WidgetState

OK_FULL = ForwardMsg.FINISHED_SUCCESSFULLY
OK_FRAGMENT = ForwardMsg.FINISHED_FRAGMENT_RUN_SUCCESSFULLY

# Text on screen that means a student is not having a normal time of it.
TROUBLE = (
    "Can't reach the assessment database",
    "You had this assessment open in more than one place",
    "Your teacher changed this assessment",
    "This attempt could not be opened",
    "no longer yours to take",
    "Time ran out",
    "signed out because",
    "didn't save",
)


# Phrases from TROUBLE that the scenario being run expects to see.
EXPECTED: set[str] = set()

# Fragment ids are derived from the code, so every session shares them.
FRAGMENT_NAMES: dict[str, str] = {}


class Disconnected(Exception):
    pass


class AppError(Exception):
    """The run that answered a click drew a traceback or an error box instead."""


def user_key(widget_id: str) -> str:
    """`$$ID-<hash>-<key>` -> `<key>`."""
    parts = widget_id.split("-", 2)
    return parts[2] if len(parts) == 3 else widget_id


class Recorder:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.samples: list[tuple[float, str, float, int]] = []  # (at, action, seconds, user)
        self.errors: list[tuple[float, int, str]] = []
        self.users: dict[int, dict] = {}
        self.health: list[tuple[float, float | None]] = []
        self.cpu: list[tuple[float, float]] = []

    def now(self) -> float:
        return time.monotonic() - self.t0

    def sample(self, user: int, action: str, seconds: float) -> None:
        self.samples.append((self.now(), action, seconds, user))

    def error(self, user: int, text: str) -> None:
        self.errors.append((self.now(), user, text))


class Tab:
    """One Streamlit session, kept the way the frontend keeps it."""

    def __init__(self, base: str, user: int, rec: Recorder) -> None:
        self.base = base.rstrip("/")
        self.user = user
        self.rec = rec
        self.ws = None
        self.elements: dict[tuple, list] = {}  # delta path -> [run id, fragment id, kind, proto]
        self.values: dict[str, WidgetState] = {}
        self.page_hash = ""
        self.run_id = ""
        self.run_fragments: tuple = ()
        self.timers: dict[str, tuple[float, asyncio.Task]] = {}
        self.auto_sent: dict[str, float] = {}
        self.changed = asyncio.Condition()
        self.finishes = 0
        self.closed = False
        self.seen_trouble: set[str] = set()
        self.failures = 0  # tracebacks and error boxes drawn so far
        self.history: list[str] = []  # the last few messages, for explaining a stuck answer

    # ------------------------------------------------------------------ connection

    async def open(self) -> None:
        url = self.base.replace("https://", "wss://").replace("http://", "ws://") + "/_stcore/stream"
        parsed = urlparse(self.base)
        self.ws = await websockets.connect(
            url, subprotocols=["streamlit"], origin=f"{parsed.scheme}://{parsed.netloc}",
            max_size=None, open_timeout=60, ping_interval=20, ping_timeout=60, close_timeout=5,
        )
        self.reader = asyncio.create_task(self._read())

    async def close(self) -> None:
        for _, task in self.timers.values():
            task.cancel()
        self.timers.clear()
        if self.ws is not None:
            await self.ws.close()

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                msg = ForwardMsg()
                msg.ParseFromString(raw)
                finished = self._handle(msg)
                if finished:
                    async with self.changed:
                        self.finishes += 1
                        self.changed.notify_all()
        except websockets.ConnectionClosed as exc:
            if not self.closing:
                self.rec.error(self.user, f"websocket closed: {exc.rcvd.code if exc.rcvd else '?'}")
        finally:
            self.closed = True
            for _, task in self.timers.values():
                task.cancel()
            async with self.changed:
                self.changed.notify_all()

    closing = False

    # ------------------------------------------------------------------ incoming

    def _handle(self, msg: ForwardMsg) -> bool:
        kind = msg.WhichOneof("type")
        self.history.append(self._summary(msg, kind))
        del self.history[:-300]
        if kind == "new_session":
            self.run_id = msg.new_session.script_run_id
            self.run_fragments = tuple(msg.new_session.fragment_ids_this_run)
            if msg.new_session.page_script_hash:
                self.page_hash = msg.new_session.page_script_hash
        elif kind == "delta":
            delta = msg.delta
            path = tuple(msg.metadata.delta_path)
            which = delta.WhichOneof("type")
            if which == "new_element":
                element_kind = delta.new_element.WhichOneof("type")
                proto = getattr(delta.new_element, element_kind)
                self.elements[path] = [self.run_id, delta.fragment_id, element_kind, proto]
                self._watch(element_kind, proto)
            elif which == "add_block":
                self.elements[path] = [self.run_id, delta.fragment_id, "block", None]
        elif kind == "auto_rerun":
            self._arm(msg.auto_rerun.fragment_id, msg.auto_rerun.interval)
        elif kind == "stop_auto_rerun":
            for fragment_id in msg.stop_auto_rerun.fragment_ids:
                held = self.timers.pop(fragment_id, None)
                if held:
                    held[1].cancel()
        elif kind == "script_finished":
            status = msg.script_finished
            if status == OK_FULL:
                self._prune(lambda run, fragment: run != self.run_id)
                # A full run absorbs any timer rerun that was waiting on it.
                self.auto_sent.clear()
            elif status == OK_FRAGMENT:
                ran = set(self.run_fragments)
                self._prune(lambda run, fragment: fragment in ran and run != self.run_id)
                for fragment_id in ran:
                    sent = self.auto_sent.pop(fragment_id, None)
                    if sent is not None:
                        self.rec.sample(self.user, f"refresh:{FRAGMENT_NAMES.get(fragment_id, '?')}",
                                        time.monotonic() - sent)
            return status in (OK_FULL, OK_FRAGMENT)
        return False

    def _summary(self, msg: ForwardMsg, kind: str) -> str:
        if kind == "new_session":
            return f"start {msg.new_session.script_run_id[:6]} frags={[f[:6] for f in msg.new_session.fragment_ids_this_run]}"
        if kind == "delta":
            which = msg.delta.WhichOneof("type")
            inner = msg.delta.new_element.WhichOneof("type") if which == "new_element" else which
            return f"  {inner} {list(msg.metadata.delta_path)} frag={msg.delta.fragment_id[:6]}"
        if kind == "script_finished":
            return f"finish {ForwardMsg.ScriptFinishedStatus.Name(msg.script_finished)}"
        return kind

    def _prune(self, stale) -> None:
        for path in [p for p, (run, fragment, _, _) in self.elements.items() if stale(run, fragment)]:
            del self.elements[path]
        live = {getattr(proto, "id", "") for _, _, _, proto in self.elements.values() if proto is not None}
        for widget_id in [w for w in self.values if w not in live]:
            del self.values[widget_id]

    def _watch(self, kind: str, proto) -> None:
        if kind == "exception":
            self.failures += 1
            self.rec.error(self.user, f"exception: {proto.type}: {proto.message[:200]}")
            return
        body = ""
        if kind == "alert":
            body = proto.body
            if proto.format == Alert.ERROR:
                self.failures += 1
                self.rec.error(self.user, f"error alert: {body[:200]}")
                return
        elif kind == "markdown":
            body = proto.body
        for phrase in TROUBLE:
            if phrase in EXPECTED:
                continue
            if phrase in body and (phrase, self.run_id) not in self.seen_trouble:
                self.seen_trouble.add((phrase, self.run_id))
                self.rec.error(self.user, f"on screen: {phrase}")

    # ------------------------------------------------------------------ timers

    def _arm(self, fragment_id: str, interval: float) -> None:
        held = self.timers.get(fragment_id)
        if held and held[0] == interval:
            return  # the frontend keeps an existing timer with the same interval
        if held:
            held[1].cancel()
        self.timers[fragment_id] = (interval, asyncio.create_task(self._tick(fragment_id, interval)))

    async def _tick(self, fragment_id: str, interval: float) -> None:
        while not self.closed:
            await asyncio.sleep(interval)
            if self.closed:
                return
            self.auto_sent.setdefault(fragment_id, time.monotonic())
            try:
                await self.send(fragment_id=fragment_id, auto=True)
            except websockets.ConnectionClosed:
                return

    # ------------------------------------------------------------------ outgoing

    async def send(self, fragment_id: str = "", auto: bool = False, trigger: str | None = None) -> None:
        self.history.append(f">> send frag={fragment_id[:6]} auto={auto} trigger={bool(trigger)}")
        msg = BackMsg()
        state = msg.rerun_script
        state.query_string = ""
        state.page_script_hash = self.page_hash
        state.fragment_id = fragment_id
        state.is_auto_rerun = auto
        state.context_info.timezone = "Asia/Kolkata"
        state.context_info.timezone_offset = -330
        state.context_info.locale = "en-US"
        state.context_info.url = self.base + "/"
        for widget in self.values.values():
            state.widget_states.widgets.append(widget)
        if trigger:
            state.widget_states.widgets.append(WidgetState(id=trigger, trigger_value=True))
        await self.ws.send(msg.SerializeToString())

    def set_string(self, widget_id: str, value: str) -> None:
        self.values[widget_id] = WidgetState(id=widget_id, string_value=value)

    def set_strings(self, widget_id: str, values: list[str]) -> None:
        state = WidgetState(id=widget_id)
        state.string_array_value.data.extend(values)
        self.values[widget_id] = state

    async def act(self, action: str, predicate, timeout: float, fragment_id: str = "",
                  trigger: str | None = None) -> float:
        """Send one interaction and wait for a finished run whose screen satisfies `predicate`."""
        async with self.changed:
            baseline = self.finishes
            failures = self.failures
        started = time.monotonic()
        await self.send(fragment_id=fragment_id, trigger=trigger)
        deadline = started + timeout
        async with self.changed:
            while True:
                if self.closed:
                    raise Disconnected(action)
                if self.finishes > baseline and predicate():
                    break
                if self.finishes > baseline and self.failures > failures:
                    raise AppError(action)
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(action)
                try:
                    await asyncio.wait_for(self.changed.wait(), left)
                except asyncio.TimeoutError:
                    pass
        elapsed = time.monotonic() - started
        self.rec.sample(self.user, action, elapsed)
        return elapsed

    async def wait_until(self, predicate, timeout: float) -> bool:
        """Wait, sending nothing, for the screen to satisfy `predicate`."""
        deadline = time.monotonic() + timeout
        async with self.changed:
            while not predicate():
                if self.closed:
                    raise Disconnected("waiting")
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                try:
                    await asyncio.wait_for(self.changed.wait(), left)
                except asyncio.TimeoutError:
                    pass
        return True

    # ------------------------------------------------------------------ reading the screen

    def find(self, kind: str, key: str):
        for _, fragment, element_kind, proto in self.elements.values():
            if element_kind == kind and proto is not None and user_key(getattr(proto, "id", "")) == key:
                return proto, fragment
        return None, None

    def find_prefix(self, kind: str | None, prefix: str) -> list:
        found = []
        for path in sorted(self.elements):
            _, fragment, element_kind, proto = self.elements[path]
            if proto is not None and (kind is None or element_kind == kind) and \
                    user_key(getattr(proto, "id", "")).startswith(prefix):
                found.append((element_kind, proto, fragment))
        return found

    def markdown(self) -> list[str]:
        return [proto.body for _, _, kind, proto in self.elements.values() if kind == "markdown"]

    def progress(self) -> str:
        for _, _, kind, proto in self.elements.values():
            if kind == "progress":
                return proto.text
        return ""

    def save_mark(self, text: str) -> str:
        """"saved", "failed" or "" for the question whose label ends in `text`."""
        for _, fragment, kind, proto in self.elements.values():
            if proto is not None and user_key(getattr(proto, "id", "")).startswith("q-") and \
                    getattr(proto, "label", "").split(". ", 1)[-1] == text:
                for _, other, other_kind, other_proto in self.elements.values():
                    if other != fragment or other_proto is None:
                        continue
                    if other_kind == "markdown" and "Saved" in other_proto.body:
                        return "saved"
                    if other_kind == "alert" and "didn't save" in other_proto.body:
                        return "failed"
                return ""
        return ""

    def describe_question(self, text: str) -> dict:
        """What is on screen around one question -- for answers that never showed "Saved"."""
        out = {"text": text[:40], "run_fragments": list(self.run_fragments), "widgets": [],
               "history": list(self.history)}
        for path, (run, fragment, kind, proto) in sorted(self.elements.items()):
            if proto is not None and getattr(proto, "label", "").split(". ", 1)[-1] == text:
                around = [(k, (getattr(p, "body", "") or "")[:50], r == run)
                          for _, (r, f, k, p) in sorted(self.elements.items()) if f == fragment and p is not None]
                out["widgets"].append({"path": list(path), "fragment": fragment, "key": user_key(proto.id),
                                       "run": run[:8], "current_run": self.run_id[:8], "around": around})
        return out

    def answered(self) -> int:
        """The paper's own "N of M answered" -- what the server has saved, not what was clicked."""
        try:
            return int(self.progress().split(" of ", 1)[0])
        except ValueError:
            return -1


# --------------------------------------------------------------------------- one student


async def student(index: int, account: dict, password: str, quiz: dict, args, rec: Recorder,
                  launch: float) -> None:
    rng = random.Random(f"{args.seed}-{index}")
    sid = account["id"]
    record = {"student_id": sid, "email": account["email"], "outcome": "not started", "answers": {}}
    rec.users[index] = record
    quiz_id = quiz["quiz_id"]
    tab = Tab(args.base, index, rec)

    async def at(offset: float) -> None:
        delay = launch + offset - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    async def again(action: str, tries: int, step) -> None:
        """A student whose click drew an error box clicks again; so does this one."""
        for attempt in range(1, tries + 1):
            try:
                return await step()
            except (AppError, TimeoutError) as exc:
                record.setdefault("retries", []).append(f"{action} #{attempt}: {type(exc).__name__}")
                if attempt == tries:
                    raise
                await asyncio.sleep(2)

    try:
        # Arrive and see the sign-in page.
        await at(args.arrive_spread * rng.random())
        record["outcome"] = "page load"
        await tab.open()
        await tab.act("page_load", lambda: tab.find("text_input", "login-id-student")[0] is not None, args.timeout)

        # Sign in.
        await at(args.login_at + args.login_spread * rng.random())
        record["outcome"] = "sign in"

        async def sign_in():
            if tab.find("button", f"start-{quiz_id}")[0] is not None:
                return  # an earlier click got through after all
            if not await tab.wait_until(lambda: tab.find("text_input", "login-id-student")[0] is not None, 30):
                raise TimeoutError("sign-in form never came back")
            tab.set_string(tab.find("text_input", "login-id-student")[0].id, account["email"])
            tab.set_string(tab.find("text_input", "login-pw-student")[0].id, password)
            submit = tab.find("button", "FormSubmitter:login-student-Sign in")[0]
            await tab.act("sign_in", lambda: tab.find("button", f"start-{quiz_id}")[0] is not None,
                          args.timeout, trigger=submit.id)
        await again("sign_in", 3, sign_in)

        # Start the assessment.
        await at(args.start_at + args.start_spread * rng.random())
        record["outcome"] = "start"

        async def start():
            if tab.find_prefix(None, "q-"):
                return  # an earlier click got through after all
            # The dashboard redraws itself every 20 seconds; a card lost to an
            # error box comes back on the next one.
            if not await tab.wait_until(lambda: tab.find("button", f"start-{quiz_id}")[0] is not None
                                        or bool(tab.find_prefix(None, "q-")), 45):
                raise TimeoutError("Start button never came back")
            if tab.find_prefix(None, "q-"):
                return
            button, fragment = tab.find("button", f"start-{quiz_id}")
            FRAGMENT_NAMES[fragment] = "dashboard"
            await tab.act("start_quiz", lambda: bool(tab.find_prefix(None, "q-")), args.timeout,
                          fragment_id=fragment, trigger=button.id)
        await again("start_quiz", 3, start)
        record["started"] = time.monotonic() - launch
        first = tab.find_prefix(None, "q-")[0]
        attempt_id = int(user_key(first[1].id).split("-")[1])
        record["attempt_id"] = attempt_id
        FRAGMENT_NAMES.setdefault(first[2], "question")
        for _, fragment, kind, proto in tab.elements.values():
            if kind in ("markdown", "html") and 'class="timer' in proto.body and fragment != first[2]:
                FRAGMENT_NAMES[fragment] = "clock"  # the countdown has a timer of its own

        # Answer every question, in the order this student's paper shows them.
        record["outcome"] = "answering"
        texts = [w[1].label.split(". ", 1)[1] for w in tab.find_prefix(None, "q-")]
        correct = 0
        for text in texts:
            await asyncio.sleep(rng.uniform(args.think_min, args.think_max))
            spec = quiz["key"][text]
            right = rng.random() < args.correct
            match = [w for w in tab.find_prefix(None, f"q-{attempt_id}-")
                     if w[1].label.split(". ", 1)[1] == text]
            if not match:
                raise RuntimeError(f"question vanished from the paper: {text[:40]}")
            kind, widget, fragment = match[0]
            if spec["type"] == "single":
                labels = list(widget.options)
                wanted = spec["correct"] if right else rng.choice(
                    [o.split(")", 1)[0] for o in labels if o.split(")", 1)[0] != spec["correct"]])
                choice = next(o for o in labels if o.split(")", 1)[0] == wanted)
                tab.set_string(widget.id, choice)
                given = wanted
            elif spec["type"] == "multi":
                wanted = spec["correct"] if right else spec["wrong"]
                chosen = [o for o in widget.options if o.split(")", 1)[0] in wanted]
                tab.set_strings(widget.id, chosen)
                given = [o.split(")", 1)[0] for o in chosen]
            else:
                given = spec["correct"] if right else spec["wrong"]
                tab.set_string(widget.id, given)
            record["answers"][text] = given
            correct += right
            given_so_far = len(record["answers"])
            try:
                # Confirmed by the question's own "Saved", or by the header's count
                # catching up on its next tick -- Streamlit can lose a run's last
                # messages in a race with a timer, and the header redraws anyway.
                await tab.act("answer", lambda t=text, n=given_so_far: tab.save_mark(t) != ""
                              or tab.answered() >= n, args.answer_timeout, fragment_id=fragment)
                if tab.save_mark(text) == "":
                    record["mark_missing"] = record.get("mark_missing", 0) + 1
                if tab.save_mark(text) == "failed":
                    # Said so on screen; a student would pick it again. Count it.
                    raise AppError("answer shown as not saved")
            except (AppError, TimeoutError) as exc:
                # The radio now shows this choice whether or not it was saved, so a
                # student has no reason to think anything went wrong. Carry on the
                # way they would; `seed.py verify` reports what the database kept.
                record.setdefault("unconfirmed", []).append(text)
                rec.error(index, f"answer not confirmed: {type(exc).__name__}")
                record.setdefault("debug", []).append(tab.describe_question(text))
        record["expected_score"] = correct / len(texts) * 100

        if args.let_time_run_out:
            # Leave the paper open and let the clock hand it in. Everybody who
            # started together runs out together, so this is the moment the
            # whole class submits within one refresh interval of each other.
            record["outcome"] = "waiting for time-up"
            deadline = record["started"] + args.let_time_run_out * 60
            async with tab.changed:
                while not (any("Your score:" in body for body in tab.markdown())
                           and not tab.find_prefix(None, "q-")):
                    if tab.closed:
                        raise Disconnected("time-up")
                    if time.monotonic() - launch > deadline + args.timeout:
                        raise TimeoutError("time-up never submitted the paper")
                    try:
                        await asyncio.wait_for(tab.changed.wait(), 5)
                    except asyncio.TimeoutError:
                        pass
            rec.sample(index, "time_up_to_result", time.monotonic() - launch - deadline)
            record["outcome"] = "submitted"
            record["auto_submitted"] = True
            record["submitted_at"] = rec.now()
            await asyncio.sleep(args.linger)
            return

        # Hand it in -- together, if the scenario says so.
        if args.submit_at is not None:
            slot = args.submit_at + args.submit_spread * rng.random()
            record["submit_late_by"] = max(0.0, (time.monotonic() - launch) - slot)
            await at(slot)
        record["outcome"] = "submitting"

        def scored() -> bool:
            return any("Your score:" in body for body in tab.markdown()) and not tab.find_prefix(None, "q-")

        def scored_or_asked() -> bool:
            return scored() or tab.find("button", f"submit-anyway-{attempt_id}")[0] is not None

        async def submit():
            if scored():
                return
            if not await tab.wait_until(lambda: scored() or tab.find("button", f"submit-{attempt_id}")[0] is not None
                                        or tab.find("button", f"submit-anyway-{attempt_id}")[0] is not None, 30):
                raise TimeoutError("Submit button never came back")
            if scored():
                return
            anyway, fragment = tab.find("button", f"submit-anyway-{attempt_id}")
            if anyway is None:
                button, fragment = tab.find("button", f"submit-{attempt_id}")
                await tab.act("submit", scored_or_asked, args.timeout, fragment_id=fragment, trigger=button.id)
                anyway, fragment = tab.find("button", f"submit-anyway-{attempt_id}")
            if anyway is not None and not scored():
                # "N questions are still unanswered" -- for a student who answered
                # all of them, that is the only sign an answer was lost.
                record["told_unanswered"] = True
                rec.error(index, "submit: told some questions are unanswered")
                await tab.act("submit_anyway", scored, args.timeout, fragment_id=fragment, trigger=anyway.id)
        await again("submit", 3, submit)
        record["outcome"] = "submitted"
        record["submitted_at"] = rec.now()
        # Students do not close the tab the instant they finish.
        await asyncio.sleep(args.linger)
    except (Disconnected, TimeoutError, AppError, RuntimeError, OSError, websockets.WebSocketException) as exc:
        rec.error(index, f"{record['outcome']}: {type(exc).__name__}: {exc}")
        record["failure"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # a harness bug: record it against this student only
        rec.error(index, f"{record['outcome']}: harness {type(exc).__name__}: {exc}")
        record["failure"] = f"harness {type(exc).__name__}: {exc}"
    finally:
        tab.closing = True
        await tab.close()


# --------------------------------------------------------------------------- watching


async def watch_health(args, rec: Recorder, stop: asyncio.Event, abort: asyncio.Event) -> None:
    """The server's own health endpoint: slow here means the process itself is busy.

    With --abort-after, a health check that stays slower than --abort-health
    seconds (or fails) for that long calls the whole run off. The target is
    usually somebody's live app, and a stuck one helps nobody.
    """
    bad_since = None
    async with httpx.AsyncClient(timeout=10) as http:
        while not stop.is_set():
            started = time.monotonic()
            try:
                response = await http.get(args.base.rstrip("/") + "/_stcore/health")
                rec.health.append((rec.now(), time.monotonic() - started if response.status_code == 200 else None))
            except httpx.HTTPError:
                rec.health.append((rec.now(), None))
            latest = rec.health[-1][1]
            if latest is None or latest > args.abort_health:
                bad_since = bad_since if bad_since is not None else time.monotonic()
                if args.abort_after and time.monotonic() - bad_since > args.abort_after and not abort.is_set():
                    print(f"[{rec.now():6.0f}s] health over {args.abort_health}s for {args.abort_after}s: aborting",
                          flush=True)
                    rec.error(-1, "run aborted: server health stayed bad")
                    abort.set()
            else:
                bad_since = None
            try:
                await asyncio.wait_for(stop.wait(), 2)
            except asyncio.TimeoutError:
                pass


def dump(args, rec: Recorder) -> dict:
    table = summarise(rec)
    args.out.write_text(json.dumps({
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "summary": table, "users": list(rec.users.values()), "errors": rec.errors,
        "samples": rec.samples, "health": rec.health, "cpu": rec.cpu,
    }))
    return table


async def watch_progress(rec: Recorder, stop: asyncio.Event, total: int, args=None) -> None:
    last_cpu, last_wall, last_count = time.process_time(), time.monotonic(), 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), 15)
        except asyncio.TimeoutError:
            pass
        cpu, wall = time.process_time(), time.monotonic()
        load = (cpu - last_cpu) / max(wall - last_wall, 1e-9)
        rec.cpu.append((rec.now(), load))
        last_cpu, last_wall = cpu, wall
        window = [s for s in rec.samples[last_count:]]
        last_count = len(rec.samples)
        outcomes: dict[str, int] = {}
        for user in rec.users.values():
            outcomes[user["outcome"]] = outcomes.get(user["outcome"], 0) + 1
        def p(action_prefix, q):
            values = sorted(s[2] for s in window if s[1].startswith(action_prefix))
            return f"{values[min(len(values) - 1, int(q * len(values)))]:.2f}s" if values else "-"
        health = [h for _, h in rec.health[-7:] if h is not None]
        if args is not None:
            dump(args, rec)
        print(f"[{rec.now():6.0f}s] {outcomes}  answer p50/p95 {p('answer', .5)}/{p('answer', .95)}  "
              f"clock refresh p50/p95 {p('refresh:clock', .5)}/{p('refresh:clock', .95)}  "
              f"health {max(health) if health else float('nan'):.2f}s  errors {len(rec.errors)}  "
              f"harness cpu {load:.0%}", flush=True)


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    if not values:
        return float("nan")
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def summarise(rec: Recorder) -> dict:
    by_action: dict[str, list[float]] = {}
    for _, action, seconds, _ in rec.samples:
        by_action.setdefault(action, []).append(seconds)
    table = {}
    for action, values in sorted(by_action.items()):
        table[action] = {"n": len(values), "p50": percentile(values, .5), "p90": percentile(values, .9),
                         "p95": percentile(values, .95), "p99": percentile(values, .99), "max": max(values),
                         "mean": statistics.fmean(values)}
    return table


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="app root, e.g. https://quiz.batchllm-workspace.info (Community Cloud: <app>/~/+)")
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("--quiz", type=Path, required=True)
    parser.add_argument("--users", type=int, default=35)
    parser.add_argument("--arrive-spread", type=float, default=30, help="tabs open over this many seconds")
    parser.add_argument("--login-at", type=float, default=0)
    parser.add_argument("--login-spread", type=float, default=30)
    parser.add_argument("--start-at", type=float, default=60, help="everyone presses Start from here...")
    parser.add_argument("--start-spread", type=float, default=10, help="...within this many seconds")
    parser.add_argument("--think-min", type=float, default=5)
    parser.add_argument("--think-max", type=float, default=15)
    parser.add_argument("--submit-at", type=float, default=None,
                        help="submit together from this offset (default: each as soon as they finish)")
    parser.add_argument("--submit-spread", type=float, default=60)
    parser.add_argument("--correct", type=float, default=0.7)
    parser.add_argument("--let-time-run-out", type=float, default=None, metavar="MINUTES",
                        help="never press Submit; wait for the quiz's MINUTES-long clock to hand it in")
    parser.add_argument("--linger", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--answer-timeout", type=float, default=45)
    parser.add_argument("--max-seconds", type=float, default=None, help="stop the whole run after this long")
    parser.add_argument("--abort-health", type=float, default=5, help="health slower than this counts as bad")
    parser.add_argument("--abort-after", type=float, default=None,
                        help="call the run off once health has been bad this many seconds")
    parser.add_argument("--seed", default="mcq")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.let_time_run_out:
        EXPECTED.add("Time ran out")

    accounts = json.loads(args.accounts.read_text())
    quiz = json.loads(args.quiz.read_text())
    assigned = set(quiz["students"])
    roster = [s for s in accounts["students"] if s["id"] in assigned][:args.users]
    if len(roster) < args.users:
        raise SystemExit(f"only {len(roster)} students are assigned to this quiz")

    rec = Recorder()
    stop = asyncio.Event()
    abort = asyncio.Event()
    watchers = [asyncio.create_task(watch_health(args, rec, stop, abort)),
                asyncio.create_task(watch_progress(rec, stop, len(roster), args))]
    launch = time.monotonic()
    tasks = [asyncio.create_task(student(i, account, accounts["password"], quiz, args, rec, launch))
             for i, account in enumerate(roster)]
    everyone = asyncio.create_task(asyncio.wait(tasks))
    called_off = asyncio.create_task(abort.wait())
    await asyncio.wait({everyone, called_off}, timeout=args.max_seconds, return_when=asyncio.FIRST_COMPLETED)
    called_off.cancel()
    done = {task for task in tasks if task.done()}
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        # Out of time, or called off: whoever is mid-paper is recorded where they got to.
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    everyone.cancel()
    for index, task in enumerate(tasks):
        if task in done and task.exception() is not None:
            rec.error(index, f"harness: {type(task.exception()).__name__}: {task.exception()}")
    if pending:
        print(f"stopped at --max-seconds with {len(pending)} students unfinished")
    stop.set()
    await asyncio.gather(*watchers)

    table = dump(args, rec)
    print("\naction                n     p50    p90    p95    p99    max")
    for action, row in table.items():
        print(f"{action:20s} {row['n']:5d} {row['p50']:6.2f} {row['p90']:6.2f} {row['p95']:6.2f} "
              f"{row['p99']:6.2f} {row['max']:6.2f}")
    outcomes: dict[str, int] = {}
    for user in rec.users.values():
        outcomes[user["outcome"]] = outcomes.get(user["outcome"], 0) + 1
    print("outcomes:", outcomes)
    print(f"errors: {len(rec.errors)}")
    for at, user, text in rec.errors[:40]:
        print(f"  [{at:6.0f}s] student {user}: {text}")
    health = [h for _, h in rec.health if h is not None]
    print(f"health endpoint: max {max(health) if health else float('nan'):.2f}s, "
          f"failures {sum(1 for _, h in rec.health if h is None)}")
    print(f"harness cpu peak: {max((c for _, c in rec.cpu), default=0):.0%} of one core")
    print(f"raw results in {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
