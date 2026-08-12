from __future__ import annotations

from datetime import datetime

import pytest

from order_service.order import InvalidTransitionError, OrderState, TerminalStateError
from order_service.payment import AuthorizeResult, CompleteResult, VoidResult
from order_service.service import OrderService

from .fakes import FakeFulfillmentService, FakePaymentProcessor

ORDER_INPUT = dict(amount_cents=12_000, currency="USD", customer_id="cust_1")


@pytest.fixture
def rig():
    payment = FakePaymentProcessor()
    fulfillment = FakeFulfillmentService()
    service = OrderService(payment, fulfillment)
    return service, payment, fulfillment


def test_happy_path(rig):
    service, _payment, _fulfillment = rig
    created = service.create(**ORDER_INPUT)
    assert created.state == OrderState.INITIALIZED

    authorized = service.authorize_payment(created.id)
    assert authorized.state == OrderState.PAYMENT_AUTHORIZED

    completed = service.complete(created.id)
    assert completed.state == OrderState.COMPLETE

    states = [t.to_state for t in completed.history]
    assert states == [OrderState.INITIALIZED, OrderState.PAYMENT_AUTHORIZED, OrderState.COMPLETE]
    for t in completed.history:
        datetime.fromisoformat(t.at)  # every entry is timestamped and parseable


def test_payment_decline_rejects_with_no_cleanup(rig):
    service, payment, _fulfillment = rig
    order = service.create(**ORDER_INPUT)
    payment.authorize_result = AuthorizeResult(authorized=False, decline_reason="card_declined")

    result = service.authorize_payment(order.id)

    assert result.state == OrderState.REJECTED
    assert payment.void_calls == 0  # no cleanup needed for a decline
    assert result.history[-1].error == "card_declined"


def test_rejected_order_is_terminal(rig):
    service, payment, _fulfillment = rig
    order = service.create(**ORDER_INPUT)
    payment.authorize_result = AuthorizeResult(authorized=False, decline_reason="card_declined")
    service.authorize_payment(order.id)

    with pytest.raises(TerminalStateError):
        service.complete(order.id)


def test_completion_failure_with_successful_void_cancels_order(rig):
    service, payment, fulfillment = rig
    order = service.create(**ORDER_INPUT)
    service.authorize_payment(order.id)

    fulfillment.complete_result = CompleteResult(completed=False, error="no_tickets_available")
    payment.void_result = VoidResult(voided=True)

    result = service.complete(order.id)

    assert result.state == OrderState.CANCELLED
    assert payment.void_calls == 1
    assert result.history[-1].error == "no_tickets_available"


def test_completion_failure_with_failed_void_needs_attention(rig):
    service, payment, fulfillment = rig
    order = service.create(**ORDER_INPUT)
    service.authorize_payment(order.id)

    fulfillment.complete_result = CompleteResult(completed=False, error="no_tickets_available")
    payment.void_result = VoidResult(voided=False, error="processor_timeout")

    result = service.complete(order.id)

    assert result.state == OrderState.NEEDS_ATTENTION
    assert payment.void_calls == 1
    error = result.history[-1].error
    assert "no_tickets_available" in error
    assert "processor_timeout" in error


def test_cannot_complete_before_payment_authorized(rig):
    service, _payment, _fulfillment = rig
    order = service.create(**ORDER_INPUT)

    with pytest.raises(InvalidTransitionError):
        service.complete(order.id)


def test_cannot_reauthorize_an_already_authorized_order(rig):
    service, _payment, _fulfillment = rig
    order = service.create(**ORDER_INPUT)
    service.authorize_payment(order.id)

    with pytest.raises(InvalidTransitionError):
        service.authorize_payment(order.id)
