"""Order state machine: states, legal transitions, and history."""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class OrderState(str, Enum):
    INITIALIZED = "initialized"
    PAYMENT_AUTHORIZED = "payment_authorized"
    COMPLETE = "complete"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    NEEDS_ATTENTION = "needs_attention"


TERMINAL_STATES = frozenset(
    {OrderState.COMPLETE, OrderState.REJECTED, OrderState.CANCELLED, OrderState.NEEDS_ATTENTION}
)

# anything not listed here is an illegal move
VALID_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.INITIALIZED: frozenset({OrderState.PAYMENT_AUTHORIZED, OrderState.REJECTED}),
    OrderState.PAYMENT_AUTHORIZED: frozenset(
        {OrderState.COMPLETE, OrderState.CANCELLED, OrderState.NEEDS_ATTENTION}
    ),
    OrderState.COMPLETE: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.NEEDS_ATTENTION: frozenset(),
}


def is_valid_transition(from_state: OrderState, to_state: OrderState) -> bool:
    return to_state in VALID_TRANSITIONS[from_state]


class InvalidTransitionError(Exception):
    def __init__(self, from_state: OrderState, to_state: OrderState):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Invalid transition: {from_state.value} -> {to_state.value}")


class OrderNotFoundError(Exception):
    def __init__(self, order_id: str):
        self.order_id = order_id
        super().__init__(f"Order not found: {order_id}")


class TerminalStateError(Exception):
    def __init__(self, order_id: str, state: OrderState):
        self.order_id = order_id
        self.state = state
        super().__init__(f"Order {order_id} is already in terminal state '{state.value}'")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StateTransition:
    from_state: OrderState
    to_state: OrderState
    reason: str
    at: str = field(default_factory=_now)
    error: Optional[str] = None  # set when this transition was itself caused by a failure

    def to_dict(self) -> dict:
        d = {
            "from": self.from_state.value,
            "to": self.to_state.value,
            "reason": self.reason,
            "at": self.at,
        }
        if self.error is not None:
            d["error"] = self.error
        return d


_id_lock = threading.Lock()
_id_counter = itertools.count(1)
_id_counter_day = datetime.now(timezone.utc).strftime("%Y%m%d")


def _next_id() -> str:
    global _id_counter, _id_counter_day
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    with _id_lock:
        if today != _id_counter_day:
            _id_counter_day = today
            _id_counter = itertools.count(1)
        seq = next(_id_counter)
    return f"ord_{today}_{seq:03d}"


@dataclass
class Order:
    id: str
    amount_cents: int
    currency: str
    customer_id: str
    state: OrderState
    history: list[StateTransition]
    created_at: str

    @classmethod
    def create(cls, amount_cents: int, currency: str, customer_id: str) -> "Order":
        now = _now()
        order = cls(
            id=_next_id(),
            amount_cents=amount_cents,
            currency=currency,
            customer_id=customer_id,
            state=OrderState.INITIALIZED,
            history=[],
            created_at=now,
        )
        order.history.append(
            StateTransition(
                from_state=OrderState.INITIALIZED,
                to_state=OrderState.INITIALIZED,
                reason="order created",
                at=now,
            )
        )
        return order

    def apply_transition(self, to_state: OrderState, reason: str, error: Optional[str] = None) -> None:
        if not is_valid_transition(self.state, to_state):
            raise InvalidTransitionError(self.state, to_state)
        self.history.append(
            StateTransition(from_state=self.state, to_state=to_state, reason=reason, error=error)
        )
        self.state = to_state

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "amount_cents": self.amount_cents,
            "currency": self.currency,
            "customer_id": self.customer_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "history": [t.to_dict() for t in self.history],
        }
