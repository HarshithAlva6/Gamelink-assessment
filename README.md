# gametime-checkout-orders

A small order state machine with stage-dependent failure recovery, built as a
take-home prototype for a checkout backend problem.

## What I built

An `Order` moves through a fixed set of states:

```
initialized ──authorize──> payment_authorized ──complete──> complete
     │                            │
     └─(declined)──> rejected     ├─(completion fails, void succeeds)──> cancelled
                                   └─(completion fails, void also fails)──> needs_attention
```

- **`order_service/order.py`** — the state machine itself: the `OrderState` enum,
  the adjacency list of legal transitions (`VALID_TRANSITIONS`), and `Order`,
  which appends a timestamped `StateTransition` to its own history on every
  move and refuses illegal ones (`InvalidTransitionError`). This is the only
  place that knows what's a legal move; everything else just calls
  `apply_transition` and lets it enforce the rules.
- **`order_service/payment.py`** — `PaymentProcessor` and `FulfillmentService`
  are ABCs (stubbed interfaces), each with an "always succeeds" default
  implementation so the server boots without a real payment/ticketing
  integration. Tests use configurable fakes instead (`tests/fakes.py`).
- **`order_service/service.py`** — `OrderService` is where the *recovery
  logic* lives, separate from the state machine's *legality* rules:
  - Payment declined → `rejected`. No void, no cleanup — nothing was ever
    authorized.
  - Completion fails after authorization → void the payment. If the void
    succeeds → `cancelled`, with the completion error recorded on the
    transition. If the void *also* fails → `needs_attention`, with **both**
    errors concatenated into the transition record. This is the one case
    that isn't a clean recovery: money may still be held, tickets weren't
    issued, and automation can't resolve it — so it's surfaced, not silently
    downgraded to `cancelled`.
- **`order_service/server.py`** — a tiny stdlib-only HTTP API (no framework;
  four routes doesn't need one):
  - `POST /orders` — create an order
  - `POST /orders/<id>/authorize` — attempt payment authorization
  - `POST /orders/<id>/complete` — attempt completion (fulfillment)
  - `GET /orders/<id>` — current state + full history

Domain errors (`OrderNotFoundError`, `TerminalStateError`,
`InvalidTransitionError`) map to `404`/`409`/`400` respectively rather than
generic 500s, since "you tried an illegal transition" is a client error, not
a server fault.

## How to run it

Requires Python 3.10+.

```bash
pip install -r requirements.txt
python -m pytest -v
```

Run the server:

```bash
python -m order_service.server
# listening on http://localhost:3000
```

Exercise it:

```bash
curl -X POST localhost:3000/orders \
  -d '{"amount_cents":5000,"currency":"USD","customer_id":"c1"}'

curl -X POST localhost:3000/orders/<id>/authorize
curl -X POST localhost:3000/orders/<id>/complete
curl localhost:3000/orders/<id>
```

The default server wiring uses the "always succeeds" stubs, so it only
exercises the happy path. The interesting failure branches are covered by
tests, which inject fakes that can be told to decline/fail/void-fail on
demand — see `tests/test_order_service.py`.

## Tests

`tests/test_order_service.py` covers the four required scenarios plus two
transition-legality guards:

1. Happy path: `initialized → payment_authorized → complete`
2. Payment decline: `rejected`, and confirms `void` was never called
3. Completion failure, void succeeds: `cancelled`, with the completion error
   recorded
4. Completion failure, void also fails: `needs_attention`, with both errors
   present in the record
5. Rejecting an order is terminal — no further transitions allowed
6. Can't `complete` before `authorize`, can't `authorize` twice

## Tradeoffs I made

- **In-memory storage, single process.** No database — a `dict` guarded by a
  `threading.Lock` (the stdlib's `ThreadingHTTPServer` dispatches each
  request on its own thread, so the shared order map needs a lock even
  though there's no real persistence). Fine for a prototype; the first thing
  I'd change for anything real is persisting `Order` + its history to a
  table, since the history *is* the audit trail this problem cares about.
- **Payment and fulfillment are separate interfaces.** A completion failure
  is a fulfillment concern (e.g., no tickets left); the void it triggers is
  a payment concern. Keeping them separate means the void-failure path is
  driven purely by `PaymentProcessor.void()`, not tangled into fulfillment
  logic.
- **No retries.** A failed void goes straight to `needs_attention` rather
  than retrying with backoff. For a 3-hour scope I'd rather surface the
  partial failure honestly than build a half-finished retry policy that
  hides how often it's actually needed.
- **`needs_attention` is a dead end in code.** There's no transition out of
  it because resolving it (manual refund, retrying the void out-of-band,
  etc.) is an operational action, not something the state machine should
  guess at. I did make sure the *reason it happened* is fully captured in
  the history so a human has what they need.
- **Stdlib HTTP, no framework.** Four routes and no auth/middleware needs
  didn't justify a dependency. I'd reach for a real framework the moment
  there's more than a handful of routes or any actual request validation
  beyond "did the JSON parse."
- **No idempotency keys.** A double-click on "authorize" or "complete" in
  the real checkout flow could double-charge. Right now the state machine's
  transition guard actually protects against that already (once
  `payment_authorized`, a second `authorize` call raises
  `InvalidTransitionError` rather than re-authorizing) — but an explicit
  idempotency key on the request would be a cleaner, more standard
  guarantee, and is one of the first things I'd add.

## What I'd do differently with more time

- Persist orders (Postgres + an `order_state_transitions` table) so the
  history survives a restart and is queryable — "show me all orders stuck
  in `needs_attention` in the last 24h" is exactly the kind of query
  ops/support would want.
- A background reconciliation job that retries failed voids on a schedule,
  only falling back to `needs_attention` after N attempts — right now it
  gives up after a single try.
- Idempotency keys on `authorize`/`complete` so retried client requests
  (e.g. a mobile client retrying on a flaky network) can't double-fire a
  side effect.
- Metrics/alerting on `needs_attention` specifically — that's the state
  that should page someone, since it represents money that isn't cleanly
  accounted for.
- Optimistic concurrency (a version/`ETag` on `Order`) instead of a single
  global lock, since a real system would have many orders in flight
  concurrently and a global lock becomes a bottleneck.
- Real payment/fulfillment adapters behind the existing interfaces —
  the whole point of stubbing them was to make that a drop-in change.
