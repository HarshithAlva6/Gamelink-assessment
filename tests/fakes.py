"""Configurable fakes so tests can control authorize/complete/void outcomes independently."""

from __future__ import annotations

from order_service.payment import (
    AuthorizeResult,
    CompleteResult,
    FulfillmentService,
    PaymentProcessor,
    VoidResult,
)


class FakePaymentProcessor(PaymentProcessor):
    def __init__(self) -> None:
        self.authorize_result = AuthorizeResult(authorized=True)
        self.void_result = VoidResult(voided=True)
        self.authorize_calls = 0
        self.void_calls = 0

    def authorize(self, order_id: str, amount_cents: int, currency: str) -> AuthorizeResult:
        self.authorize_calls += 1
        return self.authorize_result

    def void(self, order_id: str) -> VoidResult:
        self.void_calls += 1
        return self.void_result


class FakeFulfillmentService(FulfillmentService):
    def __init__(self) -> None:
        self.complete_result = CompleteResult(completed=True)
        self.complete_calls = 0

    def complete(self, order_id: str) -> CompleteResult:
        self.complete_calls += 1
        return self.complete_result
