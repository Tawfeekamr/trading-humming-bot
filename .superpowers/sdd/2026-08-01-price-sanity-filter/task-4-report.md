# Task 4 report

## Implemented

- Added `PriceFilter`, `PriceVerifier`, Binance verifier state, and a bounded async verification request/result channel to `Engine`.
- Order-book updates now call `PriceFilter::observe` with the complete `OrderBook`; only accepted books replace the engine book.
- Suspect updates use `validated_mid`, last-good mid, configured tolerance, and timeout. Verification is single-flight per symbol and runs outside the WebSocket event arm.
- Engine tracks per-symbol generations and the latest pending suspect book. A stale result re-observes the latest candidate; it is accepted, hard-rejected, or starts a fresh verification without clearing a newer pending candidate.
- Confirmed results resolve the filter and reprocess/publish the pending book; denied or unavailable results fail closed and retain the last-good book.
- Non-reduce orders for suspect symbols are vetoed after pair normalization in both strategy submission and API placement; reduce-only exits continue through.
- Added focused engine tests for suspect entry veto/exit bypass, confirmed-book reprocessing, stale latest-book reprocessing, and API entry veto.

## Focused verification

- `cargo test --lib engine::tests --no-fail-fast` (13 passed)
- `cargo check -p trading-engine-core` (passed; existing warnings only)

## Concerns

- Binance verification remains network-dependent in production; timeout, unavailable, queue-full, and worker-channel failures all retain the last-good book and do not block the event loop.
- The verifier worker serializes network calls globally, while per-symbol generations prevent duplicate/stale verification from overwriting newer order books.
