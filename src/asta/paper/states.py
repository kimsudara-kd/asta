"""Paper-session state machine (M2-B). Every permitted transition is listed; anything else is
refused. No transition produces an external effect: a session only ever changes LOCAL simulated
state.

    CREATED ──initialize──▶ INITIALIZING ──ready──▶ READY ──start──▶ RUNNING
    RUNNING ⇄ PAUSED (pause / resume, resume only after validation)
    RUNNING ──worker/restart──▶ RECOVERING ──verified──▶ RUNNING | PAUSED
    RUNNING ──risk trip──▶ RISK_HALTED ──operator re-arm (daily trip only, later UTC day)──▶ PAUSED
    any non-terminal ──stop──▶ STOPPED          any ──inconsistency──▶ FAILED
    STOPPED and FAILED are terminal.
"""

from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    CREATED = "CREATED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RECOVERING = "RECOVERING"
    RISK_HALTED = "RISK_HALTED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


S = SessionState

TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    S.CREATED: frozenset({S.INITIALIZING, S.STOPPED, S.FAILED}),
    S.INITIALIZING: frozenset({S.READY, S.STOPPED, S.FAILED}),
    S.READY: frozenset({S.RUNNING, S.STOPPED, S.FAILED}),
    S.RUNNING: frozenset({S.PAUSED, S.RECOVERING, S.RISK_HALTED, S.STOPPED, S.FAILED}),
    S.PAUSED: frozenset({S.RUNNING, S.RECOVERING, S.STOPPED, S.FAILED}),
    S.RECOVERING: frozenset({S.RUNNING, S.PAUSED, S.RISK_HALTED, S.STOPPED, S.FAILED}),
    S.RISK_HALTED: frozenset({S.PAUSED, S.RECOVERING, S.STOPPED, S.FAILED}),
    S.STOPPED: frozenset(),
    S.FAILED: frozenset(),
}

TERMINAL = frozenset({S.STOPPED, S.FAILED})
#: States in which the worker evaluates strategies and posts simulated fills.
PROCESSING = frozenset({S.RUNNING})
#: States in which market bars are still recorded/marked but no new orders may be created.
OBSERVING = frozenset({S.PAUSED, S.RISK_HALTED, S.RECOVERING})


class IllegalTransition(Exception):
    pass


def check_transition(src: SessionState, dst: SessionState) -> None:
    if dst not in TRANSITIONS[src]:
        raise IllegalTransition(f"{src} -> {dst} is not permitted")
