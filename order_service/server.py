"""Tiny hand-rolled HTTP API (stdlib only -- four routes doesn't need a framework).

  POST /orders                  {amount_cents, currency, customer_id} -> Order
  GET  /orders/<id>              -> Order (current state + full history)
  POST /orders/<id>/authorize    -> Order (attempts payment authorization)
  POST /orders/<id>/complete     -> Order (attempts completion / fulfillment)
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .order import InvalidTransitionError, OrderNotFoundError, TerminalStateError
from .payment import AlwaysApprovePaymentProcessor, AlwaysSucceedFulfillmentService
from .service import OrderService


def _error_response(err: Exception) -> tuple[int, dict]:
    if isinstance(err, OrderNotFoundError):
        return 404, {"error": str(err)}
    if isinstance(err, (TerminalStateError, InvalidTransitionError)):
        return 409, {"error": str(err)}
    return 400, {"error": str(err)}


def make_handler(service: OrderService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # quiet the default access log
            pass

        def _send_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw) if raw else {}

        def _dispatch(self, handler_fn: Callable[[], None]) -> None:
            try:
                handler_fn()
            except Exception as err:  # noqa: BLE001 - translated into an HTTP response, not swallowed
                status, body = _error_response(err)
                self._send_json(status, body)

        def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            parts = [p for p in self.path.split("/") if p]

            if len(parts) == 1 and parts[0] == "orders":
                def action():
                    body = self._read_json_body()
                    order = service.create(
                        amount_cents=int(body["amount_cents"]),
                        currency=str(body["currency"]),
                        customer_id=str(body["customer_id"]),
                    )
                    self._send_json(201, order.to_dict())

                return self._dispatch(action)

            if len(parts) == 3 and parts[0] == "orders" and parts[2] == "authorize":
                return self._dispatch(
                    lambda: self._send_json(200, service.authorize_payment(parts[1]).to_dict())
                )

            if len(parts) == 3 and parts[0] == "orders" and parts[2] == "complete":
                return self._dispatch(lambda: self._send_json(200, service.complete(parts[1]).to_dict()))

            self._send_json(404, {"error": "not_found"})

        def do_GET(self) -> None:  # noqa: N802
            parts = [p for p in self.path.split("/") if p]

            if len(parts) == 2 and parts[0] == "orders":
                return self._dispatch(lambda: self._send_json(200, service.get(parts[1]).to_dict()))

            self._send_json(404, {"error": "not_found"})

    return Handler


def main() -> None:
    service = OrderService(AlwaysApprovePaymentProcessor(), AlwaysSucceedFulfillmentService())
    port = int(os.environ.get("PORT", 3000))
    server = ThreadingHTTPServer(("localhost", port), make_handler(service))
    print(f"gametime-checkout-orders listening on http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
