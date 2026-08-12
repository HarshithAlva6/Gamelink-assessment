"""Owns order storage and picks the recovery path when a stage fails."""

from __future__ import annotations

import threading

from .order import (
    InvalidTransitionError,
    Order,
    OrderNotFoundError,
    OrderState,
    TERMINAL_STATES,
    TerminalStateError,
)
from .payment import FulfillmentService, PaymentProcessor


class OrderService:
    def __init__(self, payment_processor: PaymentProcessor, fulfillment_service: FulfillmentService):
        self._payment_processor = payment_processor
        self._fulfillment_service = fulfillment_service
        self._orders: dict[str, Order] = {}
        self._lock = threading.Lock()  # ThreadingHTTPServer dispatches each request on its own thread

    def create(self, amount_cents: int, currency: str, customer_id: str) -> Order:
        order = Order.create(amount_cents, currency, customer_id)
        with self._lock:
            self._orders[order.id] = order
        return order

    def get(self, order_id: str) -> Order:
        with self._lock:
            order = self._orders.get(order_id)
        if order is None:
            raise OrderNotFoundError(order_id)
        return order

    def list(self) -> list[Order]:
        with self._lock:
            return list(self._orders.values())

    def authorize_payment(self, order_id: str) -> Order:
        """A decline just rejects the order -- nothing was ever authorized, so no cleanup."""
        order = self.get(order_id)
        with self._lock:
            self._assert_stage(order, OrderState.INITIALIZED)

            result = self._payment_processor.authorize(order.id, order.amount_cents, order.currency)
            if result.authorized:
                order.apply_transition(OrderState.PAYMENT_AUTHORIZED, "payment authorized")
            else:
                order.apply_transition(
                    OrderState.REJECTED, "payment declined", result.decline_reason or "declined"
                )
            return order

    def complete(self, order_id: str) -> Order:
        """A completion failure isn't a decline: money's already authorized, so it
        has to be voided before the order can be cancelled. If the void fails
        too, we can't claim a clean cancellation -- go to NEEDS_ATTENTION and
        keep both errors instead of picking one.
        """
        order = self.get(order_id)
        with self._lock:
            self._assert_stage(order, OrderState.PAYMENT_AUTHORIZED)

            result = self._fulfillment_service.complete(order.id)
            if result.completed:
                order.apply_transition(OrderState.COMPLETE, "order completed")
                return order

            completion_error = result.error or "completion failed"
            void_result = self._payment_processor.void(order.id)

            if void_result.voided:
                order.apply_transition(
                    OrderState.CANCELLED, "completion failed, payment voided", completion_error
                )
            else:
                combined_error = (
                    f"completion failed ({completion_error}); "
                    f"void also failed ({void_result.error or 'unknown void error'})"
                )
                order.apply_transition(
                    OrderState.NEEDS_ATTENTION,
                    "completion and void both failed - manual resolution required",
                    combined_error,
                )
            return order

    def _assert_stage(self, order: Order, expected: OrderState) -> None:
        if order.state in TERMINAL_STATES:
            raise TerminalStateError(order.id, order.state)
        if order.state != expected:
            raise InvalidTransitionError(order.state, expected)
