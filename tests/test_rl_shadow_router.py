"""Focused isolation and validation tests for the PPO shadow router."""
from __future__ import annotations

import json

import pytest

from src.rl.action_map import ACTION_TO_ENGINE_SIZE
from src.rl.live_router import decode_action
from src.rl.shadow_journal import log_decision
from src.rl.shadow_schema import ShadowRoutingDecision, validate_decision


def _journal_worker(path: str, action: int, barrier) -> None:
    barrier.wait()
    decision = ShadowRoutingDecision.from_action(
        "ETH-USDT", action, decode_action(action), f"ppo-{action}", model_sha256=f"sha-{action}"
    )
    log_decision(path, decision)


def _decision(**overrides):
    value = ShadowRoutingDecision.from_action(
        "ETH-USDT",
        0,
        decode_action(0),
        "ppo-test",
        timestamp_ms=1_000,
        observation_age_ms=100,
        model_sha256="abc123",
    ).to_dict()
    value.update(overrides)
    return value


def test_shadow_decision_uses_canonical_action_map():
    decision = ShadowRoutingDecision.from_action(
        "ETH-USDT", 8, decode_action(8), "ppo-test", model_sha256="sha"
    )
    assert decision.engine == ACTION_TO_ENGINE_SIZE[8][0]
    assert decision.size_mult == ACTION_TO_ENGINE_SIZE[8][1]
    assert decision.mode == "shadow"


def test_shadow_validation_rejects_invalid_and_stale_decisions():
    assert validate_decision(_decision(), now_ms=1_000, max_age_ms=1_000) == (
        True,
        "valid",
    )
    for invalid in (
        {"engine": "unknown"},


        {"action": 10},
        {"size_mult": 1.5},
        {"mode": "live"},
        {"timestamp_ms": -1},
    ):
        candidate = _decision(**invalid)
        ok, reason = validate_decision(candidate, now_ms=1_000, max_age_ms=1_000)
        assert not ok, reason

def test_shadow_validation_requires_model_provenance():
    for field, value in (
        ("model_version", ""),
        ("model_sha256", None),
        ("model_sha256", 123),
    ):
        ok, reason = validate_decision(
            _decision(**{field: value}), now_ms=1_000, max_age_ms=1_000
        )
        assert not ok
        assert field in reason


def test_shadow_validation_rejects_size_not_matching_action_map():
    ok, reason = validate_decision(
        _decision(size_mult=1.5), now_ms=1_000, max_age_ms=1_000
    )
    assert not ok
    assert "size multiplier" in reason


def test_atomic_journal_appends_jsonl_without_active_cache(tmp_path, monkeypatch):
    import requests

    calls = []
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: calls.append(args[0]))
    active_cache = tmp_path / "routing_cache.json"
    active_cache.write_text('{"sentinel":true}\n')
    shadow_path = tmp_path / "shadow.jsonl"

    decision = ShadowRoutingDecision.from_action(
        "ETH-USDT", 0, decode_action(0), "ppo-test", model_sha256="sha"
    )
    log_decision(shadow_path, decision)
    log_decision(shadow_path, decision)

    rows = [json.loads(line) for line in shadow_path.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["mode"] == "shadow"
    assert active_cache.read_text() == '{"sentinel":true}\n'
    assert calls == []

def test_atomic_journal_preserves_concurrent_process_records(tmp_path):
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    shadow_path = str(tmp_path / "concurrent.jsonl")
    barrier = context.Barrier(2)
    workers = [
        context.Process(target=_journal_worker, args=(shadow_path, action, barrier))
        for action in (0, 1)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
        assert worker.exitcode == 0

    rows = [json.loads(line) for line in (tmp_path / "concurrent.jsonl").read_text().splitlines()]
    assert {row["action"] for row in rows} == {0, 1}


def test_live_router_shadow_loop_never_posts_or_writes_active_cache(
    tmp_path, monkeypatch
):
    """One deterministic cycle proves the default isolation boundary."""
    import pandas as pd
    import requests
    import sys
    import types

    from src.rl import live_router
    from src.rl import router
    fake_features = types.ModuleType("src.rl.features")
    fake_features.FEATURE_COLS = [f"feature_{i}" for i in range(17)]
    monkeypatch.setitem(sys.modules, "src.rl.features", fake_features)
    class StopLoop(Exception):
        pass

    active_cache = tmp_path / "routing_cache.json"
    active_cache.write_text('{"sentinel":true}\n')
    shadow_path = tmp_path / "shadow.jsonl"
    calls = []
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: calls.append(args[0]))
    monkeypatch.setattr(live_router, "_fetch_live_klines", lambda *args, **kwargs: object())
    monkeypatch.setattr(live_router, "_get_equity", lambda *args, **kwargs: 10_000.0)
    monkeypatch.setattr(live_router, "build_observation", lambda *args, **kwargs: object())
    row = pd.Series(
        {column: 0.0 for column in fake_features.FEATURE_COLS},
        name=pd.Timestamp.now(tz="UTC"),
    )
    monkeypatch.setattr(
        router,
        "PPORouter",
        lambda path: type("FakeRouter", (), {"predict": lambda self, obs: 0})(),
    )
    monkeypatch.setattr(
        fake_features,
        "compute_features",
        lambda frame: pd.DataFrame(
            [row.to_numpy()], columns=fake_features.FEATURE_COLS, index=[row.name]
        ),
        raising=False,
    )
    monkeypatch.setattr(
        __import__("time"),
        "sleep",
        lambda seconds: (_ for _ in ()).throw(StopLoop()),
    )

    with pytest.raises(StopLoop):
        live_router.run_loop(
            "ETHUSDT",
            str(tmp_path / "model.zip"),
            "http://engine",
            bar_seconds=0,
            shadow=True,
            shadow_path=str(shadow_path),
        )

    assert calls == []
    assert active_cache.read_text() == '{"sentinel":true}\n'
    assert len(shadow_path.read_text().splitlines()) == 1
