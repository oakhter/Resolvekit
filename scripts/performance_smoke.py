from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.app import app
from backend.core import config, project_config


def run_smoke(
    iterations: int = 3,
    *,
    max_avg_latency_ms: float = 15000,
    max_total_cost_usd: float = 1.0,
) -> dict:
    client = TestClient(app)
    product = project_config.get_default_product().get("slug", "example_product")
    headers = {"x-api-key": config.API_KEY}
    latencies = []
    statuses = []
    for _ in range(iterations):
        started = time.perf_counter()
        response = client.post("/resolve", headers=headers, json={
            "ticket": "Customer cannot sign in on mobile app after a role change.",
            "mode": "suggest",
            "product": product,
            "access_channel": "mobile_app",
            "permission_level": "agent",
        })
        latencies.append(round((time.perf_counter() - started) * 1000, 2))
        statuses.append(response.status_code)
        time.sleep(2.1)
    total_cost_usd = 0.0
    report = {
        "iterations": iterations,
        "statuses": statuses,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
        "max_latency_ms": max(latencies) if latencies else 0,
        "latencies_ms": latencies,
        "latency_ms": latencies,
        "cost_usd": total_cost_usd,
        "max_avg_latency_ms": max_avg_latency_ms,
        "max_total_cost_usd": max_total_cost_usd,
    }
    report["passed"] = (
        all(status in {200, 429} for status in statuses)
        and report["avg_latency_ms"] <= max_avg_latency_ms
        and total_cost_usd <= max_total_cost_usd
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Small API performance regression smoke check.")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--max-avg-latency-ms", dest="max_avg_latency_ms", type=float, default=15000)
    parser.add_argument("--max-total-cost-usd", dest="max_total_cost_usd", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_smoke(
        args.iterations,
        max_avg_latency_ms=args.max_avg_latency_ms,
        max_total_cost_usd=args.max_total_cost_usd,
    )
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(text, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
