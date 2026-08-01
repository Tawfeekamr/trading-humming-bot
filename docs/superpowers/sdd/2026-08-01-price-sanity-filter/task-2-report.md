# Task 2 Report: Pure PriceFilter

Implemented `trading-engine-core/src/price_filter.rs` and exported it from `trading-engine-core/src/lib.rs`.

## Behavior

- `observe` consumes a complete `OrderBook`, validates every price and quantity level, rejects empty sides and crossed books, and derives the mid from the maximum bid and minimum ask.
- Invalid books return `HardReject` without creating or mutating per-symbol state.
- Per-symbol rolling mid windows trigger `SuspectNewVerify` on abnormal jumps and retain the last-good mid/book.
- Suspect books return `HoldSuspect`; configured consecutive in-band books self-heal the pair.
- `Confirmed` accepts the validated pending suspect book and metadata only when the supplied scalar agrees with that book; `Denied` and `Unavailable` retain the last-good level and suspect state.
- `validated_mid` exposes the same full-book validation/calculation for verification callers, including unsorted levels.
- A hard reject while suspect resets the recovery streak without changing the last-good book.
- State is isolated by symbol.

Inline tests cover warmup, normal in-band updates, malformed levels, crossed books, rolling-stdev adaptation, self-healing, hard-reject recovery reset, scalar/book mismatch protection, all verification outcomes, pair isolation, real moves, and the July 31 BNB phantom scenario.

## Verification

Focused command:

```text
cd trading-engine-core && cargo test --lib price_filter --no-fail-fast
```

Result: 15 tests passed; 205 tests filtered.

## Concerns

- Engine/connector integration and async verification remain intentionally untouched for later tasks.
- `resolve_verify` derives the candidate mid from the pending full book and rejects a mismatched scalar, so future callers cannot accept a different level than the observed book.
