"""The robot service (skills/docs/skills.md): a long-running process, the only long-lived DDS owner of the robot's
sport state and motion. It keeps the latest rt/sportmodestate message (real) or the stub's state (sim), answers
state reads and stream subscriptions on a Unix socket, and runs motion primitives for the armed step's lease
through its one sport client (real: SportClient; sim: stub.SimRobot), which also sends every stop.

    python -m skills.robot_service

Started by the dispatcher; prints one ``ready`` line on stdout once it accepts connections."""

from __future__ import annotations

import fcntl
import math
import os
import queue
import signal
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from multiprocessing.connection import Listener
from types import FrameType
from typing import Any, NoReturn, Protocol, cast

from contract import wire
from contract.env import ROBOT_SOCKET, robot_lock_path
from contract.schema import RobotState
from skills import backend, process, robot_ipc, stub
from skills.state_store import StateStore

READY_LINE = "ready"
STARTUP_TIMEOUT_S = 10.0  # SDK import, DDS init and subscription on the lab machine (tunable)
STOP_GRACE_S = 2.0  # SIGTERM to SIGKILL (tunable)
SIM_PERIOD_S = 0.02  # the sim publishes the stub state at 50 Hz, the rate assumed for the robot (tunable)
LEASE_POLL_S = 0.02  # how often the lease deadline is checked (tunable)
EOF_POLL_S = 0.02  # how often a skill connection is checked for EOF while its primitive runs (tunable)
YAW_STOP_MARGIN_RAD = math.radians(3.0)  # a yaw target stops this early, for the coast (tunable, skills/docs/robot.md)
MAX_STATE_AGE_S = 0.3  # a targeted move stops on older state (tunable, skills/docs/skills.md)
SOCKET_MODE = 0o600
EXIT_CONFIG_ERROR = 2
EXIT_LOCKED = 3

_lock_fd: int | None = None  # held for the process lifetime; the kernel releases it on any exit


def acquire_lock(path: str) -> bool:
    """Take the exclusive lock on ``path`` for this process; False if another process holds it."""
    global _lock_fd
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return False
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _lock_fd = fd
    return True


# --- state sources ----------------------------------------------------------------


def _same(state: RobotState) -> RobotState:
    return state


def _publish_sim(store: StateStore) -> None:
    try:
        store.put(stub.sample_state())
    except Exception:  # a bad state file or objects env: no message, as on a lost link
        traceback.print_exc(file=sys.stderr)


def _simulate(store: StateStore) -> None:
    while True:
        _publish_sim(store)
        time.sleep(SIM_PERIOD_S)


def start_source(name: str) -> tuple[StateStore, object]:
    """The store fed by the backend's state messages, and what must stay referenced to keep them coming.
    real: the rt/sportmodestate subscriber; sim (stub): a thread sampling the stub state every SIM_PERIOD_S."""
    if name == "real":
        from skills import real

        store = StateStore(real.state_from_msg)
        return store, real.subscribe_state(store.put)
    store = StateStore(_same)
    thread = threading.Thread(target=_simulate, args=(store,), name="sim-state", daemon=True)
    thread.start()
    return store, thread


# --- motion: one sport client, the lease and the motion loop -----------------------


class SportClient(Protocol):
    """The calls the service makes; each returns an int, 0 = success (skills/docs/robot.md)."""

    def Move(self, vx: float, vy: float, vyaw: float) -> int: ...
    def StopMove(self) -> int: ...
    def StandDown(self) -> int: ...
    def Stretch(self) -> int: ...


Wait = Callable[[float, threading.Event], Any]  # the motion clock: wait seconds or until the event is set


@dataclass
class Lease:
    """The armed step: motion is accepted with ``token`` until ``deadline`` (Motion's clock), on the one
    connection that first used it (``conn``)."""

    token: str
    deadline: float
    conn: wire.Conn | None = None


@dataclass
class Job:
    """One primitive for the motion loop; ``abort`` is set (under Motion's lock) when its lease ends."""

    run: Callable[[Job], dict]
    abort: threading.Event = field(default_factory=threading.Event)
    abort_reason: str = ""
    done: threading.Event = field(default_factory=threading.Event)
    reply: dict | None = None


class NoState(Exception):
    """No state message with imu_rpy, or one older than MAX_STATE_AGE_S."""


class YawTracker:
    """The yaw change since the first read, from ``imu_rpy[2]`` in the store: per-read deltas wrapped to
    (-pi, pi] and summed, so crossing +-pi counts as the short way round. Raises NoState on a read."""

    def __init__(self, store: StateStore) -> None:
        self._store = store
        self._last = self._yaw()
        self.change = 0.0

    def _yaw(self) -> float:
        reading = self._store.read()
        if reading is None or reading.state.imu_rpy is None:
            raise NoState("no state message with imu_rpy")
        if (reading.state.age_ms or 0.0) > MAX_STATE_AGE_S * 1000.0:
            raise NoState(f"the latest state is {reading.state.age_ms:.0f} ms old")
        return reading.state.imu_rpy[2]

    def update(self) -> float:
        yaw = self._yaw()
        delta = math.remainder(yaw - self._last, math.tau)
        self.change += math.pi if delta == -math.pi else delta
        self._last = yaw
        return self.change


class Motion:
    """The sport client, the lease and the motion loop thread. ``_lock`` guards the lease, the control
    connection, the running job and the StopMove record, and serialises every Move and every action's commit
    with the abort. ``clock`` sets and checks the lease deadline; StopMove timing always uses real time."""

    def __init__(
        self,
        client: SportClient,
        wait: Wait,
        store: StateStore,
        *,
        sim: bool,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._wait = wait
        self._clock = clock
        self._store = store
        self._sim = sim
        self._lock = threading.Lock()
        self._lease: Lease | None = None
        self._control: wire.Conn | None = None
        self._job: Job | None = None
        self._jobs: queue.Queue[Job] = queue.Queue()
        self._stops_running = 0  # StopMoves decided and not yet returned
        self._stops_done = threading.Condition(self._lock)
        self._stop_failed: robot_ipc.StopFailure | None = None  # the lease's latest StopMove, if it failed
        self._stop_error = False  # the sim's stop_error fault: every StopMove fails until the next arm
        self._rejected = False  # the sim's rejected fault: every Move and action fails until the next arm

    def start(self) -> None:
        threading.Thread(target=self._loop, name="motion-loop", daemon=True).start()
        threading.Thread(target=self._watch_deadline, name="lease-deadline", daemon=True).start()

    # --- stopping ------------------------------------------------------------------------

    def stop_move(self, reason: str) -> tuple[int | None, float]:
        """StopMove now, from the calling thread; returns its code (None if it raised) and its time in ms."""
        with self._lock:
            self._stops_running += 1
        return self._send_stop(reason)

    def _send_stop(self, reason: str) -> tuple[int | None, float]:
        """StopMove for a stop already counted in ``_stops_running``; records it and wakes a waiting disarm."""
        t = time.monotonic()
        try:
            ret: int | None = stub.STUB_ERR_INJECTED if self._stop_error else self._client.StopMove()
        except Exception:
            traceback.print_exc(file=sys.stderr)
            ret = None
        ms = round((time.monotonic() - t) * 1000.0, 3)
        print(f"robot service: StopMove ({reason}) returned {ret} in {ms} ms", file=sys.stderr, flush=True)
        with self._lock:
            self._stop_failed = None if ret == 0 else robot_ipc.StopFailure(reason=reason, ret=ret, stop_call_ms=ms)
            self._stops_running -= 1
            self._stops_done.notify_all()
        return ret, ms

    def _end_lease_locked(self, reason: str) -> bool:
        """End the lease and abort the running primitive (caller holds the lock); True if the lease was used."""
        used = self._lease is not None and self._lease.conn is not None
        self._lease = None
        if self._job is not None and not self._job.abort.is_set():
            self._job.abort_reason = reason
            self._job.abort.set()
        return used

    def halt(self, reason: str) -> tuple[int | None, float]:
        """End any lease and StopMove, whatever the state: the stop op, the control connection lost, the
        dispatcher gone."""
        with self._lock:
            self._end_lease_locked(reason)
            self._stops_running += 1
        return self._send_stop(reason)

    def _end_lease(self, reason: str, *, conn: wire.Conn | None = None, lease: Lease | None = None) -> None:
        """End the lease (only if bound to ``conn``, or only if it is ``lease``, when given); StopMove if it was
        used for motion."""
        with self._lock:
            current = self._lease
            if (
                current is None
                or (conn is not None and current.conn is not conn)
                or (lease is not None and lease is not current)
            ):
                return
            used = self._end_lease_locked(reason)
            if used:
                self._stops_running += 1  # counted under the lock, so a disarm waits for it
        if used:
            self._send_stop(reason)

    def shutdown(self) -> None:
        self._end_lease("service stopping")

    # --- control connection ----------------------------------------------------------------

    def claim_control(self, conn: wire.Conn) -> bool:
        with self._lock:
            if self._control is not None:
                return False
            self._control = conn
            return True

    def is_control(self, conn: wire.Conn) -> bool:
        with self._lock:
            return self._control is conn

    def connection_closed(self, conn: wire.Conn) -> None:
        """A connection is gone: the control connection stops the robot always, the lease's only if used."""
        with self._lock:
            control = self._control is conn
            if control:
                self._control = None
        if control:
            self.halt("control connection closed")
        else:
            self._end_lease("lease connection closed", conn=conn)

    def arm(self, token: str, timeout_s: float) -> None:
        with self._lock:
            self._stop_failed, self._stop_error, self._rejected = None, False, False
        self._end_lease("re-armed")
        with self._lock:
            self._lease = Lease(token=token, deadline=self._clock() + timeout_s)

    def disarm(self) -> robot_ipc.StopFailure | None:
        """End the lease, wait for every StopMove already decided, and return the lease's latest if it failed."""
        self._end_lease("disarmed")
        with self._lock:
            self._stops_done.wait_for(lambda: self._stops_running == 0)
            return self._stop_failed

    def _watch_deadline(self) -> None:
        while True:
            time.sleep(LEASE_POLL_S)
            self.end_expired_lease()

    def end_expired_lease(self) -> bool:
        """End the lease if its deadline has passed; True if it did. The deadline thread calls it every
        LEASE_POLL_S."""
        with self._lock:
            lease = self._lease
            expired = lease is not None and self._clock() >= lease.deadline
        if expired:
            print("robot service: lease deadline passed", file=sys.stderr, flush=True)
            self._end_lease("lease deadline", lease=lease)
        return expired

    # --- primitives --------------------------------------------------------------------------

    def submit(self, conn: wire.Conn, token: object, run: Callable[[Job], dict]) -> Job:
        """Queue a primitive for the lease's connection; raises Refusal without a valid lease."""
        with self._lock:
            lease = self._lease
            if lease is None:
                raise Refusal(robot_ipc.ERR_NO_LEASE, "no lease is armed for this step")
            if token != lease.token:
                raise Refusal(robot_ipc.ERR_NO_LEASE, "the token does not match the armed lease")
            if lease.conn is not None and lease.conn is not conn:
                raise Refusal(robot_ipc.ERR_NO_LEASE, "another connection holds the lease")
            if self._clock() >= lease.deadline:
                raise Refusal(robot_ipc.ERR_NO_LEASE, "the lease deadline has passed")
            lease.conn = conn
            job = Job(run=run)
            self._job = job
            self._jobs.put(job)
        return job

    def _loop(self) -> None:
        while True:
            job = self._jobs.get()
            try:
                job.reply = job.run(job)
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                self.stop_move("sport client raised")
                text = f"{type(e).__name__}: {e}"
                job.reply = robot_ipc.error_message(robot_ipc.ERR_SDK_EXCEPTION, text)
            with self._lock:
                if self._job is job:
                    self._job = None
            job.done.set()

    @staticmethod
    def _aborted(job: Job) -> dict:
        return robot_ipc.error_message(robot_ipc.ERR_ABORTED, f"the lease ended: {job.abort_reason}")

    def _fault(self, fault: str | None) -> None:
        if fault == "stop_error":
            with self._lock:
                self._stop_error = True
        elif fault == "rejected":
            with self._lock:
                self._rejected = True
        elif fault in stub.CONDITIONS:  # accepted only on the sim backend (_service_fault)
            cast(stub.SimRobot, self._client).inject(cast(stub.Condition, fault))
        elif fault is not None:
            stub.apply_service_fault(fault)

    def move(
        self,
        vx: float,
        vy: float,
        vyaw: float,
        duration_s: float,
        target: robot_ipc.MoveTarget | None,
        fault: str | None,
    ) -> Callable[[Job], dict]:
        """The move primitive: Move every CMD_PERIOD_S for ``duration_s``, then StopMove. With ``target``,
        ``duration_s`` is the cap: StopMove once the measured yaw change is within YAW_STOP_MARGIN_RAD of it; without
        fresh yaw, finish open loop at |vyaw| (skills/docs/skills.md)."""
        period = robot_ipc.CMD_PERIOD_S
        cap = max(1, round(duration_s / period))

        def ticks(seconds: float) -> int:
            return round(max(0.0, seconds) / period)

        def run(job: Job) -> dict:
            sent, move_ret, limit, yaw = 0, 0, cap, None
            if target is not None:
                try:
                    yaw = YawTracker(self._store)
                except NoState as e:
                    print(f"robot service: open-loop move, {e}", file=sys.stderr, flush=True)
                    limit = min(cap, max(1, ticks(abs(target.yaw_rad) / abs(vyaw) + target.lead_s)))
            while sent < limit:
                with self._lock:
                    if job.abort.is_set():
                        break
                    ret = stub.SDK_ERR_CLIENT_SEND if self._rejected else self._client.Move(vx, vy, vyaw)
                if ret != 0:
                    move_ret = ret
                    break
                sent += 1
                if sent == 1:
                    self._fault(fault)  # mid-walk: the robot has a velocity command
                self._wait(period, job.abort)
                if target is None or yaw is None:
                    continue
                try:
                    if abs(yaw.update()) >= abs(target.yaw_rad) - YAW_STOP_MARGIN_RAD:
                        break
                except NoState as e:  # finish the rest on time; the robot is already turning
                    print(f"robot service: open-loop move from {yaw.change:.3f} rad, {e}", file=sys.stderr, flush=True)
                    limit = min(cap, sent + ticks((abs(target.yaw_rad) - abs(yaw.change)) / abs(vyaw)))
                    yaw = None
            if job.abort.is_set():
                return self._aborted(job)  # whoever ended the lease sent the StopMove
            stop_ret, _ = self.stop_move("move done" if move_ret == 0 else "Move failed")
            measured = None if target is None else {"yaw_rad": None if yaw is None else round(yaw.change, 4)}
            return robot_ipc.message(
                "move", duration_s=round(sent * period, 3), move_ret=move_ret, stop_ret=stop_ret, measured=measured
            )

        return run

    def action(self, name: str, fault: str | None) -> Callable[[Job], dict]:
        """The action primitive: one call that waits for its reply, so it is committed under the lock but sent
        outside it; a stop never waits on the reply, and one that lands during the call gets a trailing StopMove."""

        def run(job: Job) -> dict:
            self._fault(fault)
            with self._lock:
                if job.abort.is_set():
                    return self._aborted(job)
                call = (lambda: stub.SDK_ERR_API_TIMEOUT) if self._rejected else getattr(self._client, name)
            ret = call()
            if job.abort.is_set():
                self.stop_move(f"lease ended during {name}")  # the stop may have reached the robot first
                return self._aborted(job)
            return robot_ipc.message("action", ret=ret)

        return run

    @property
    def sim(self) -> bool:
        return self._sim


class Refusal(Exception):
    """A request refused with a protocol error code; ``closes``: the service closes the connection after it."""

    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.closes = code in robot_ipc.CLOSING_ERRORS


def start_motion(name: str, store: StateStore) -> Motion:
    """The service's one sport client: real: SportClient (after the state subscription's DDS init); sim:
    stub.SimRobot over the stub state file, with the stub time scale, publishing a state after each motion step."""
    if name == "real":
        from skills import real

        def wait(seconds: float, abort: threading.Event) -> None:
            abort.wait(seconds)

        motion = Motion(real.get_sport_client(), wait, store, sim=False)
    else:
        sim = stub.SimRobot()

        def sim_wait(seconds: float, abort: threading.Event) -> None:
            sim.wait(seconds, abort)
            _publish_sim(store)  # the pose moved: a fresh message, as the robot sends several per tick

        motion = Motion(sim, sim_wait, store, sim=True)
    motion.start()
    return motion


# --- connections ------------------------------------------------------------------


def _send(conn: wire.Conn, msg: dict) -> bool:
    try:
        conn.send_bytes(wire.encode(msg))
        return True
    except (OSError, ValueError):
        return False


def _number(request: dict, key: str) -> float:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (math.isfinite(value) and value >= 0):
        raise ValueError(f"{key} must be a finite number >= 0, got {value!r}")
    return float(value)


def _finite(request: dict, key: str) -> float:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number, got {value!r}")
    return float(value)


def _timeout(request: dict, key: str) -> float:
    value = _number(request, key)
    if value > robot_ipc.MAX_TIMEOUT_S:
        raise ValueError(f"{key} must be at most {robot_ipc.MAX_TIMEOUT_S:g}, got {value!r}")
    return value


def _target(request: dict) -> robot_ipc.MoveTarget | None:
    target = request.get("target")
    if target is None:
        return None
    if not isinstance(target, dict) or set(target) != {"yaw_rad", "lead_s"}:
        raise ValueError(f"target must be an object with yaw_rad and lead_s, got {target!r}")
    return robot_ipc.MoveTarget(yaw_rad=_finite(target, "yaw_rad"), lead_s=_number(target, "lead_s"))


def _service_fault(request: dict, motion: Motion) -> str | None:
    fault = request.get("fault")
    if fault is not None and (not motion.sim or fault not in stub.SERVICE_FAULTS):
        raise ValueError(f"fault {fault!r} is not a service fault of the sim backend")
    return fault


def _min_seq(request: dict) -> int | None:
    value = request.get("min_seq")
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"min_seq must be an integer or null, got {value!r}")
    return value


def stream(conn: wire.Conn, store: StateStore, period_s: float) -> None:
    """Send each new message at most once per ``period_s`` and every link change, until the client goes."""
    link, _ = store.link()
    if not _send(conn, robot_ipc.message("subscribed", period_ms=round(period_s * 1000.0, 3), link=link)):
        return
    sent_seq = 0
    while True:
        time.sleep(period_s)
        now, rx_mono = store.link()
        if now != link:
            link = now
            if not _send(conn, robot_ipc.message("link", link=link, rx_mono=rx_mono)):
                return
        reading = store.read()
        if reading is not None and reading.state.seq != sent_seq:
            sent_seq = reading.state.seq or 0
            if not _send(conn, robot_ipc.state_message("sample", reading)):
                return


def run_primitive(conn: wire.Conn, motion: Motion, request: dict, run: Callable[[Job], dict]) -> dict | None:
    """Run a primitive for this connection and return its reply; None if the connection closed meanwhile
    (the lease then ends and the robot stops)."""
    job = motion.submit(conn, request.get("token"), run)
    while not job.done.wait(EOF_POLL_S):
        try:
            if not conn.poll():
                continue
            conn.recv_bytes(robot_ipc.MAX_MESSAGE_BYTES)
        except (OSError, EOFError):
            return None
        raise Refusal(robot_ipc.ERR_BAD_REQUEST, "a request arrived while a primitive ran")
    return job.reply


def _handle(conn: wire.Conn, request: dict, store: StateStore, backend_name: str, motion: Motion) -> dict | None:
    """The reply to one request; None when the connection is done. Raises Refusal or ValueError (bad_request)."""
    op = request.get("op")
    if op == "hello":
        if request.get("role") == robot_ipc.ROLE_CONTROL and not motion.claim_control(conn):
            raise Refusal(robot_ipc.ERR_BUSY, "a control connection is already open")
        return robot_ipc.message("hello", backend=backend_name, pid=os.getpid())
    if op == "state":
        wait_s = min(_number(request, "wait_ms") / 1000.0, robot_ipc.MAX_STATE_WAIT_S)
        return robot_ipc.state_message("state", store.read(_min_seq(request), wait_s))
    if op == "subscribe":
        period_s = max(_number(request, "period_ms") / 1000.0, robot_ipc.MIN_STREAM_PERIOD_S)
        stream(conn, store, period_s)
        return None
    if op == "ping":
        return robot_ipc.message("pong")
    if op == "stop":
        ret, ms = motion.halt("stop requested")
        return robot_ipc.message("stop_sent", ret=ret, stop_call_ms=ms)
    if op in ("arm", "disarm"):
        if not motion.is_control(conn):
            raise ValueError(f"{op} is accepted only on the control connection")
        if op == "disarm":
            failed = motion.disarm()
            return robot_ipc.message("disarmed", stop_failed=None if failed is None else asdict(failed))
        token = request.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError(f"token must be a non-empty string, got {token!r}")
        timeout_s = _timeout(request, "timeout_s")
        motion.arm(token, timeout_s)
        return robot_ipc.message("armed", token=token, timeout_s=timeout_s)
    if op == "move":
        vx, vy, vyaw = (_finite(request, k) for k in ("vx", "vy", "vyaw"))
        duration_s, target = _timeout(request, "duration_s"), _target(request)
        if target is not None and vyaw == 0:
            raise ValueError("a yaw target needs a non-zero vyaw")
        run = motion.move(vx, vy, vyaw, duration_s, target, _service_fault(request, motion))
        return run_primitive(conn, motion, request, run)
    if op == "action":
        name = request.get("name")
        if name not in robot_ipc.ACTIONS:
            raise ValueError(f"name must be one of {', '.join(robot_ipc.ACTIONS)}, got {name!r}")
        return run_primitive(conn, motion, request, motion.action(name, _service_fault(request, motion)))
    raise ValueError(f"unknown op {op!r}")


def serve(conn: wire.Conn, store: StateStore, backend_name: str, motion: Motion) -> None:
    """Answer requests on one persistent connection until the client closes it; a bad request closes it.
    However it ends, a lease bound to it ends (StopMove), and the control connection's end stops the robot."""
    try:
        with conn:
            while True:
                try:
                    request = wire.decode(conn.recv_bytes(robot_ipc.MAX_MESSAGE_BYTES))
                except (OSError, EOFError):
                    return
                except ValueError as e:
                    _send(conn, robot_ipc.error_message(robot_ipc.ERR_BAD_REQUEST, f"not a JSON object: {e}"))
                    return
                if request.get("v") != robot_ipc.PROTOCOL_VERSION:
                    text = f"protocol version {request.get('v')!r} is not {robot_ipc.PROTOCOL_VERSION}"
                    _send(conn, robot_ipc.error_message(robot_ipc.ERR_PROTOCOL_VERSION, text))
                    return
                try:
                    reply = _handle(conn, request, store, backend_name, motion)
                except Refusal as e:
                    if not _send(conn, robot_ipc.error_message(e.code, str(e))) or e.closes:
                        return
                    continue
                except ValueError as e:
                    _send(conn, robot_ipc.error_message(robot_ipc.ERR_BAD_REQUEST, str(e)))
                    return
                if reply is None or not _send(conn, reply):
                    return
    finally:
        motion.connection_closed(conn)


def _on_sigterm(signum: int, frame: FrameType | None) -> NoReturn:
    raise SystemExit(0)


def _fail(text: str, code: int) -> NoReturn:
    print(f"robot service: {text}", file=sys.stderr)
    sys.exit(code)


def main() -> None:
    process.capture_stdout()
    path = os.environ.get(ROBOT_SOCKET, "")
    if not path:
        _fail(f"{ROBOT_SOCKET} is not set", EXIT_CONFIG_ERROR)
    try:
        name = backend.backend_name()
    except backend.BackendNotConfigured as e:
        _fail(str(e), EXIT_CONFIG_ERROR)
    lock = robot_lock_path(path)
    if not acquire_lock(lock):
        _fail(f"another robot service holds {lock}", EXIT_LOCKED)
    try:
        store, _source = start_source(name)
        motion = start_motion(name, store)
    except backend.BackendNotConfigured as e:
        _fail(str(e), EXIT_CONFIG_ERROR)
    process.start_orphan_watchdog(on_orphan=lambda: motion.halt("dispatcher gone"))
    if os.path.exists(path):  # left by a service that was killed; the lock says none is running
        os.unlink(path)
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        with Listener(path, family=wire.FAMILY) as listener:  # closing it removes the socket file
            os.chmod(path, SOCKET_MODE)
            process.write_raw_stdout(READY_LINE + "\n")
            print(f"robot service: {name} ready on {path}", file=sys.stderr, flush=True)
            while True:
                try:
                    conn = listener.accept()
                except OSError:
                    traceback.print_exc(file=sys.stderr)
                    continue
                args = (conn, store, name, motion)
                threading.Thread(target=serve, args=args, name="robot-conn", daemon=True).start()
    finally:
        motion.shutdown()


if __name__ == "__main__":
    main()
