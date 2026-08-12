"""Payment and fulfillment are stubbed as interfaces (ABCs) so a real payment
processor (Stripe, Braintree, ...) or fulfillment system (ticket issuance) can
be dropped in later without touching the state machine or service logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class AuthorizeResult:
    authorized: bool
    decline_reason: Optional[str] = None


@dataclass
class VoidResult:
    voided: bool
    error: Optional[str] = None


@dataclass
class CompleteResult:
    completed: bool
    error: Optional[str] = None


class PaymentProcessor(ABC):
    @abstractmethod
    def authorize(self, order_id: str, amount_cents: int, currency: str) -> AuthorizeResult: ...

    @abstractmethod
    def void(self, order_id: str) -> VoidResult:
        """Releases a previously authorized (but not captured) payment. Can itself fail."""
        ...


class FulfillmentService(ABC):
    @abstractmethod
    def complete(self, order_id: str) -> CompleteResult:
        """Whatever 'completing' an order means: issuing tickets, capturing payment, etc."""
        ...


class AlwaysApprovePaymentProcessor(PaymentProcessor):
    """Default stub: always approves, always voids cleanly. Enough to boot the server."""

    def authorize(self, order_id: str, amount_cents: int, currency: str) -> AuthorizeResult:
        return AuthorizeResult(authorized=True)

    def void(self, order_id: str) -> VoidResult:
        return VoidResult(voided=True)


class AlwaysSucceedFulfillmentService(FulfillmentService):
    def complete(self, order_id: str) -> CompleteResult:
        return CompleteResult(completed=True)
