from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_performance_smoke_script_exists_and_has_budget_flags():
    script = ROOT / "scripts" / "performance_smoke.py"
    text = script.read_text()

    assert "max_avg_latency_ms" in text
    assert "max_total_cost_usd" in text
    assert "latency_ms" in text
    assert "cost_usd" in text


def test_duplicate_paths_have_canonical_headers():
    expected = {
        ROOT / "scripts" / "demo_doctor.sh": "Canonical release doctor",
        ROOT / "scripts" / "onboarding_doctor.py": "Onboarding-only doctor",
        ROOT / "pipeline" / "cache.py": "Canonical low-level cache",
        ROOT / "pipeline" / "orchestrator_cache.py": "Canonical orchestrator cache",
        ROOT / "configs" / "baseline.yaml": "Canonical experiment baseline",
    }

    for path, marker in expected.items():
        assert marker in path.read_text()
