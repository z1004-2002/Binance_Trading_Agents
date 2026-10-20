import math
import random

import pytest

from orchestre.portfolio.allocator_v2 import AllocatorConfig, score, target_weights

CFG = AllocatorConfig(window=60, max_weight=0.6, max_step=0.10)


def noisy(mean, vol, n=60, seed=1):
    rng = random.Random(seed)
    return [mean + rng.gauss(0, vol) for _ in range(n)]


def test_score_rewards_return_and_penalises_volatility():
    assert score(noisy(0.002, 0.01)) > score(noisy(0.002, 0.03)) > 0
    assert score(noisy(-0.002, 0.01)) == 0.0          # rendement négatif : aucun poids
    assert score([0.0] * 60) == 0.0                   # stratégie inactive : aucun poids
    assert score([0.01]) == 0.0                       # historique insuffisant


def test_better_risk_adjusted_agent_gets_the_larger_weight():
    w = target_weights({"a": noisy(0.003, 0.01), "b": noisy(0.001, 0.02)}, {"a": 1.0, "b": 1.0}, None, AllocatorConfig(max_weight=1.0, max_step=1.0))
    assert w["a"] > w["b"] > 0 and sum(w.values()) == pytest.approx(1.0)


def test_zero_multiplier_excludes_an_agent():
    w = target_weights({"a": noisy(0.003, 0.01), "b": noisy(0.003, 0.01, seed=2)}, {"a": 1.0, "b": 0.0}, None, AllocatorConfig(max_weight=1.0, max_step=1.0))
    assert "b" not in w or w["b"] == 0.0


def test_half_multiplier_halves_the_raw_preference():
    r = {"a": noisy(0.003, 0.01), "b": noisy(0.003, 0.01)}
    full = target_weights(r, {"a": 1.0, "b": 1.0}, None, AllocatorConfig(max_weight=1.0, max_step=1.0))
    half = target_weights(r, {"a": 1.0, "b": 0.5}, None, AllocatorConfig(max_weight=1.0, max_step=1.0))
    assert half["b"] < full["b"]


def test_equal_weights_when_nobody_has_a_positive_score():
    w = target_weights({"a": noisy(-0.002, 0.01), "b": noisy(-0.002, 0.01, seed=2)}, {"a": 1.0, "b": 1.0}, None, AllocatorConfig(max_weight=1.0, max_step=1.0))
    assert w["a"] == pytest.approx(0.5) and w["b"] == pytest.approx(0.5)


def test_no_eligible_agent_means_all_cash():
    assert target_weights({"a": noisy(0.003, 0.01)}, {"a": 0.0}, None, CFG) == {}


def test_weights_never_exceed_the_cap_and_the_excess_goes_to_others():
    r = {"a": noisy(0.01, 0.005), "b": noisy(0.001, 0.02), "c": noisy(0.001, 0.02, seed=5)}
    w = target_weights(r, {k: 1.0 for k in r}, None, AllocatorConfig(max_weight=0.5, max_step=1.0))
    assert all(v <= 0.5 + 1e-9 for v in w.values()) and w["a"] == pytest.approx(0.5)
    assert sum(w.values()) == pytest.approx(1.0)


def test_when_every_agent_is_capped_the_rest_stays_in_cash():
    w = target_weights({"a": noisy(0.003, 0.01), "b": noisy(0.003, 0.01, seed=2)}, {"a": 1.0, "b": 1.0}, None, AllocatorConfig(max_weight=0.3, max_step=1.0))
    assert sum(w.values()) == pytest.approx(0.6)


def test_weekly_change_is_limited_to_ten_points():
    r = {"a": noisy(0.01, 0.005), "b": noisy(-0.01, 0.005)}
    previous = {"a": 0.2, "b": 0.8}
    w = target_weights(r, {"a": 1.0, "b": 1.0}, previous, AllocatorConfig(max_weight=1.0, max_step=0.10))
    assert abs(w["a"] - 0.2) <= 0.10 + 1e-9 and abs(w.get("b", 0.0) - 0.8) <= 0.10 + 1e-9


def test_step_limited_weights_are_scaled_down_if_they_exceed_one():
    r = {"a": noisy(0.01, 0.005), "b": noisy(0.01, 0.005, seed=2)}
    w = target_weights(r, {"a": 1.0, "b": 1.0}, {"a": 0.9, "b": 0.9}, AllocatorConfig(max_weight=1.0, max_step=0.10))
    assert sum(w.values()) <= 1.0 + 1e-9


def test_property_weights_are_valid_for_random_inputs():
    rng = random.Random(42)
    for _ in range(200):
        agents = [f"s{i}" for i in range(rng.randint(1, 5))]
        r = {a: noisy(rng.uniform(-0.003, 0.004), rng.uniform(0.002, 0.04), seed=rng.randint(0, 9999)) for a in agents}
        m = {a: rng.choice([0.0, 0.5, 1.0]) for a in agents}
        prev = {a: rng.random() / len(agents) for a in agents} if rng.random() < 0.5 else None
        cfg = AllocatorConfig(max_weight=rng.uniform(0.3, 1.0), max_step=rng.uniform(0.05, 0.3))
        w = target_weights(r, m, prev, cfg)
        assert sum(w.values()) <= 1.0 + 1e-9
        assert all(0.0 <= v <= cfg.max_weight + 1e-9 for v in w.values())
        assert all(m[a] > 0 or v == 0 for a, v in w.items() if prev is None)
        if prev:
            assert all(abs(v - prev.get(a, 0.0)) <= cfg.max_step + 1e-9 or v < prev.get(a, 0.0) for a, v in w.items())


def test_a_lowered_cap_applies_immediately_despite_the_step_limit():
    r = {"a": noisy(0.003, 0.01)}
    w = target_weights(r, {"a": 1.0}, {"a": 0.9}, AllocatorConfig(max_weight=0.4, max_step=0.10))
    assert w["a"] == pytest.approx(0.4)
