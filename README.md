# gametime-assessment

A small order state machine with stage-dependent failure recovery, built as a
take-home prototype for a checkout backend problem.

## What I built and why

The interesting part of this problem isn't the happy path, it's that
"something failed" means different things at different stages. The same
event (completion fails) needs a different recovery depending on whether
money has moved yet. So the design keeps two concerns apart: which
transitions are *legal* lives in `order.py`, and which recovery is
*correct* for a given failure lives in `service.py`. That split is why
adding a new failure mode means adding a policy branch rather than editing
the state machine.

The one case that resists automation is a failed void: the order is
neither completed nor cleanly reversed, and money may still be held. Rather
than pick the more convenient lie and call it `cancelled`, it gets its own
state.

An `Order` moves through a fixed set of states:

```
initialized ──authorize──> payment_authorized ──complete──> complete
     │                            │
     └─(declined)──> rejected     ├─(completion fails, void succeeds)──> cancelled
                                   └─(completion fails, void also fails)──> needs_attention
```

- **`order_service/order.py`** -- the state machine itself: the `OrderState` enum,
  the adjacency list of legal transitions (`VALID_TRANSITIONS`), and `Order`,
  which appends a timestamped `StateTransition` to its own history on every
  move and refuses illegal ones (`InvalidTransitionError`). This is the only
  place that knows what's a legal move; everything else just calls
  `apply_transition` and lets it enforce the rules.
- **`order_service/payment.py`** -- `PaymentProcessor` and `FulfillmentService`
  are ABCs (stubbed interfaces), each with an "always succeeds" default
  implementation so the server boots without a real payment/ticketing
  integration. Tests use configurable fakes instead (`tests/fakes.py`).
- **`order_service/service.py`** -- `OrderService` is where the *recovery
  logic* lives, separate from the state machine's *legality* rules:
  - Payment declined → `rejected`. No void, no cleanup -- nothing was ever
    authorized.
  - Completion fails after authorization → void the payment. If the void
    succeeds → `cancelled`, with the completion error recorded on the
    transition. If the void *also* fails → `needs_attention`, with **both**
    errors concatenated into the transition record. This is the one case
    that isn't a clean recovery: money may still be held, tickets weren't
    issued, and automation can't resolve it -- so it's surfaced, not silently
    downgraded to `cancelled`.
- **`order_service/server.py`** -- a tiny stdlib-only HTTP API (no framework;
  four routes doesn't need one):
  - `POST /orders` -- create an order
  - `POST /orders/<id>/authorize` -- attempt payment authorization
  - `POST /orders/<id>/complete` -- attempt completion (fulfillment)
  - `GET /orders/<id>` -- current state + full history

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
demand -- see `tests/test_order_service.py`.

## Tests

`tests/test_order_service.py` covers the four required scenarios plus
transition-legality and concurrency guards:

1. Happy path: `initialized → payment_authorized → complete`
2. Payment decline: `rejected`, and confirms `void` was never called
3. Completion failure, void succeeds: `cancelled`, with the completion error
   recorded
4. Completion failure, void also fails: `needs_attention`, with both errors
   present in the record
5. Rejecting an order is terminal -- no further transitions allowed
6. Can't `complete` before `authorize`, can't `authorize` twice
7. Two threads racing to complete the same order: the lock serializes them,
   the loser gets `TerminalStateError` instead of double-processing, and
   both threads return within a timeout -- proving there's no deadlock,
   not just asserting it

## Tradeoffs I made

- **In-memory storage, single process.** No database -- a `dict` guarded by a
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
  `InvalidTransitionError` rather than re-authorizing) -- but an explicit
  idempotency key on the request would be a cleaner, more standard
  guarantee, and is one of the first things I'd add.

## What I'd do differently with more time

- Persist orders (Postgres + an `order_state_transitions` table) so the
  history survives a restart and is queryable -- "show me all orders stuck
  in `needs_attention` in the last 24h" is exactly the kind of query
  ops/support would want.
- A background reconciliation job that retries failed voids on a schedule,
  only falling back to `needs_attention` after N attempts -- right now it
  gives up after a single try.
- Idempotency keys on `authorize`/`complete` so retried client requests
  (e.g. a mobile client retrying on a flaky network) can't double-fire a
  side effect.
- Metrics/alerting on `needs_attention` specifically -- that's the state
  that should page someone, since it represents money that isn't cleanly
  accounted for.
- Optimistic concurrency (a version/`ETag` on `Order`) instead of a single
  global lock, since a real system would have many orders in flight
  concurrently and a global lock becomes a bottleneck.
- Real payment/fulfillment adapters behind the existing interfaces --
  the whole point of stubbing them was to make that a drop-in change.

## How I used AI on this

I used Claude Code to write first-draft code under fairly close direction:
I made the calls, it typed, and I reviewed and corrected what came back.
The decisions below were the ones that shaped the result.

**Calls I made**

- **Language.** The first draft came back in TypeScript and I rejected it.
  The role lists Go and "similar languages (Python, Java, ...)" for backend
  work, with TypeScript only on the React side, so Python was the better
  fit. (Go was my first choice; it isn't installed on this machine and
  setting it up wasn't worth the time budget.)
- **Scope.** I checked the ask against the JD and deliberately did *not*
  build a UI -- the assignment asks for a service and an API, and the brief
  says not to over-engineer. React on the JD describes the team, not this
  problem.
- **Order IDs.** The draft generated opaque `ord_<hex><counter>` values.
  I changed them to `ord_YYYYMMDD_NNN`, which is what a support agent would
  actually read back over the phone, and had the daily counter reset so
  they stay short.
- **Concurrency.** The failure modes in the brief are all sequential, but
  the interesting question is what happens when two requests race the same
  order. I pushed on that specifically -- whether the design could deadlock
  and what a double-complete does -- which is what produced
  `test_no_double_complete_race` and the reentrancy note in the tradeoffs.
- **Comment density.** The first pass was over-commented, with lines that
  just restated the code beneath them. I had them cut back to explaining
  *why* -- the lock, the decline-vs-completion-failure distinction -- and
  reviewed the result against SOLID rather than accepting the structure
  on faith.

**What I kept from the draft**

The three-way split (transition legality in `order.py`, recovery policy in
`service.py`, external contracts in `payment.py`) was drafted rather than
specified by me, but I kept it deliberately: it's the separation that lets
the void-failure path be driven purely by `PaymentProcessor.void()` without
leaking into fulfillment logic, and it's what makes the fakes in
`tests/fakes.py` drop-in.

**How I validated it**

- I ran the suite (8 tests green) and drove the live server with curl
  end-to-end rather than trusting the tests alone -- including manually
  racing two parallel `complete` requests and confirming one `200` and one
  `409`.
- The concurrency test is the clearest case of the draft being wrong. It
  originally asserted the losing thread would see `InvalidTransitionError`
  and that fulfillment would be called twice. Running it showed both were
  wrong: the loser gets `TerminalStateError`, and fulfillment is called
  exactly once, because the lock blocks the second thread before it ever
  reaches that call. The conclusion held but the reasoning didn't, so I
  corrected the assertions to match actual behavior.
- I satisfied myself on the deadlock question by reasoning about the lock
  directly rather than taking the answer on faith: one non-reentrant lock,
  never acquired nested, so no wait cycle can form. That reasoning is also
  what surfaced the reentrancy hazard I flagged in the tradeoffs -- a
  future payment adapter calling back into `OrderService` would break the
  property, and that's worth knowing before someone wires one in.
