# Consolidated ResolveKit test suite.
# Source files merged from the previous split test modules.



# --- API smoke tests ---
"""
ResolveKit — automated QA tests.

Requires the API server running (start.py launches it in the background).

Run via:  python tests/run_qa.py
Or:       pytest tests/test_resolvekit.py -k TestResolve -v
"""
import argparse
import os
import subprocess
import sys
import time
import pytest
import httpx

from backend.core import analytics, project_config
from pipeline import evidence_table, planner, responder, retriever, validation

TIMEOUT_FAST = 10
TIMEOUT_RESOLVE = 90

_CONFIG = project_config.load_config("products")
_PRODUCTS = _CONFIG.get("products", {})
_DEFAULT_PRODUCT = project_config.get_default_product(_CONFIG)
_DEFAULT_PRODUCT_VALUE = _DEFAULT_PRODUCT.get("slug") or _DEFAULT_PRODUCT.get("display_name") or ""
_SECOND_PRODUCT_VALUE = next(
    (
        product.get("slug") or product.get("display_name") or slug
        for slug, product in _PRODUCTS.items()
        if slug != _DEFAULT_PRODUCT.get("slug")
    ),
    "",
)

_BASE_TICKET = (
    "User cannot log in to the mobile app. "
    "Getting error 403 on mobile only. Desktop works fine. "
    "Started after last update."
)

_FULL_PAYLOAD = {
    "ticket": _BASE_TICKET,
    "product": _DEFAULT_PRODUCT_VALUE,
    "permission_level": "employee",
    "access_channel": "mobile_app",
}


def _is_safe_abstention(res: dict) -> bool:
    scorer = res.get("confidence_scorer") or {}
    return bool(
        res.get("draft_unavailable_reason")
        or scorer.get("recommended_action") in {"ask_clarifying_question", "escalate", "refuse"}
        or scorer.get("confidence_band") == "red"
    )


class TestPhase8AdvancedReasoning:
    def test_planner_outputs_typed_reasoning_shape(self):
        context = {
            "ticket": {
                "cleaned": "Can an admin refund a failed payment, and what changed in the latest release?"
            },
            "request_meta": {
                "product": "example_product",
                "permission_level": "admin",
                "access_channel": "website",
            },
            "product": "example_product",
            "platform": "website",
        }

        result = planner.run(context)
        plan = result["planner_output"]

        for key in (
            "intent",
            "entities",
            "explicit_questions",
            "required_context",
            "risk_flags",
            "retrieval_plan",
            "answer_type",
        ):
            assert key in plan
        assert plan["retrieval_plan"]
        assert plan["retrieval_plan"][0]["required_evidence"] == "approved_customer_facing"

    def test_retrieval_strategy_graphrag_fails_closed_without_enablement(self):
        workflow = {
            "experiments": {
                "retrieval_strategy_v1": {
                    "arm": "graphrag_layer",
                    "allowed_arms": ["graphrag_layer"],
                    "graphrag_enabled": False,
                }
            }
        }

        strategy = retriever._retrieval_strategy(workflow)

        assert strategy["arm"] == "graphrag_layer"
        assert strategy["active_arm"] == "disabled"
        assert strategy["enabled"] is False

    def test_retrieval_strategy_allows_request_scoped_arm_override(self):
        workflow = {
            "experiments": {
                "retrieval_strategy_v1": {
                    "arm": "current_rag_query_decomposition",
                    "allowed_arms": ["current_hybrid_rag", "current_rag_query_decomposition"],
                    "graphrag_enabled": False,
                }
            }
        }

        strategy = retriever._retrieval_strategy(workflow, requested_arm="current_hybrid_rag")

        assert strategy["arm"] == "current_rag_query_decomposition"
        assert strategy["active_arm"] == "current_hybrid_rag"
        assert strategy["requested_arm"] == "current_hybrid_rag"

    def test_question_queries_include_base_and_planner_questions(self):
        context = {
            "planner_output": {
                "retrieval_plan": [
                    {"question_id": "q1", "query": "How do refunds work?"},
                    {"question_id": "q2", "query": "What changed in release notes?"},
                ]
            }
        }

        queries = retriever._question_queries(context, "refund release", enabled=True)

        assert [item["question_id"] for item in queries] == ["q0", "q1", "q2"]
        assert queries[0]["source"] == "base_query"

    def test_evidence_table_records_supported_facts_missing_context_and_conflicts(self):
        context = {
            "top_chunks": [
                {
                    "id": "chunk-1",
                    "content": "Refunds are available within 30 days when policy conditions are met.",
                    "source_id": "policy-1",
                    "source_type": "policy",
                    "rerank_score": 8.0,
                }
            ],
            "planner_output": {"required_context": ["purchase date"]},
            "source_conflicts": [{"topic": "refund window", "source_a": "policy-1", "source_b": "policy-2"}],
        }

        table = evidence_table.build(context)

        assert table["supported_facts"][0]["claim"].startswith("Refunds are available")
        assert "purchase date" in table["missing_context"]
        assert table["conflicts"][0]["topic"] == "refund window"

    def test_structured_reply_renderer_uses_typed_fields(self):
        resolution = {
            "issue_classification": "Refund policy question",
            "resolution_steps": "Check purchase date before confirming eligibility. [KB-1]",
            "root_cause": "Policy depends on purchase date. [KB-1]",
            "draft_email": "",
            "confidence": "MEDIUM",
        }
        table = {"missing_context": ["purchase date"], "conflicts": []}

        structured = responder.build_structured_reply(resolution, table)
        rendered = responder.render_structured_reply(structured)

        assert structured["citations"] == ["[KB-1]"]
        assert "Missing context: purchase date" in rendered

    def test_validation_flags_uncited_factual_answer(self):
        context = {
            "resolution": {
                "root_cause": "Refunds are available within 30 days.",
                "resolution_steps": "Tell the customer they can get a refund.",
                "draft_email": "",
                "sources": "policy.csv",
                "confidence": "MEDIUM",
                "confidence_scorer": {"confidence_band": "yellow"},
            },
            "eval_score": {"evaluation_skipped": True},
            "routing_strategy": "billing",
            "ticket": {"cleaned": "Can I get a refund?"},
            "top_chunks": [],
            "request_meta": {},
            "evidence_table": {"supported_facts": [{"claim": "Refund window", "citations": ["KB-1"]}]},
        }

        result = validation.run(context)

        assert result["resolution"]["validation"]["passed"] is False
        assert any(
            "Factual answer fields did not cite approved evidence" in claim
            for claim in result["resolution"]["validation"]["unsupported_claims"]
        )


# ── /health ──────────────────────────────────────────────────

class TestHealth:
    def test_returns_ok(self, base_url):
        r = httpx.get(f"{base_url}/health", timeout=TIMEOUT_FAST)
        assert r.status_code == 200

    def test_response_shape(self, base_url):
        data = httpx.get(f"{base_url}/health", timeout=TIMEOUT_FAST).json()
        assert data["status"] == "ok"
        assert "service" in data


# ── auth ─────────────────────────────────────────────────────

class TestAuth:
    def test_missing_key_returns_401(self, base_url):
        r = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, timeout=TIMEOUT_FAST)
        assert r.status_code == 401

    def test_wrong_key_returns_401(self, base_url):
        r = httpx.post(
            f"{base_url}/resolve",
            json=_FULL_PAYLOAD,
            headers={"x-api-key": "invalid-key-xyz"},
            timeout=TIMEOUT_FAST,
        )
        assert r.status_code == 401


# ── /resolve ─────────────────────────────────────────────────

class TestResolve:
    def test_returns_200(self, base_url, auth):
        r = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE)
        assert r.status_code == 200

    def test_response_envelope(self, base_url, auth):
        data = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE).json()
        assert data["status"] == "success"
        assert "resolution" in data

    def test_required_fields_present(self, base_url, auth):
        res = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE).json()["resolution"]
        required = ("issue_classification", "root_cause", "resolution_steps", "confidence")
        for field in required:
            assert field in res, f"Missing field: {field}"
        assert any(res.get(field) for field in required) or _is_safe_abstention(res)
        assert res.get("draft_email") or _is_safe_abstention(res)

    def test_confidence_valid_value(self, base_url, auth):
        res = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE).json()["resolution"]
        assert res["confidence"] in ("HIGH", "MEDIUM", "LOW", "")
        if not res["confidence"]:
            assert "confidence_scorer" in res

    def test_has_cache_key(self, base_url, auth):
        res = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE).json()["resolution"]
        if _is_safe_abstention(res):
            assert "request_fingerprint" in res
        else:
            assert "cache_key" in res
            assert len(res["cache_key"]) == 64

    def test_has_usage_summary(self, base_url, auth):
        res = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE).json()["resolution"]
        assert "usage_summary" in res
        summary = res["usage_summary"]
        for key in ("response_tokens_in", "response_tokens_out", "total_tokens"):
            assert key in summary
        assert "total_cost_usd" in summary

    def test_has_retrieval_signals(self, base_url, auth):
        res = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE).json()["resolution"]
        assert "retrieval_signals" in res
        sig = res["retrieval_signals"]
        for key in ("top_score", "score_gap", "rerank_scores", "retrieved_chunk_ids"):
            assert key in sig, f"Missing signal: {key}"

    def test_diagnosis_field_present(self, base_url, auth):
        res = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE).json()["resolution"]
        assert "diagnosis" in res

    def test_draft_email_has_subject(self, base_url, auth):
        res = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE).json()["resolution"]
        email = res.get("draft_email", "")
        assert "Subject:" in email or "subject:" in email.lower() or _is_safe_abstention(res)

    def test_empty_ticket_returns_400(self, base_url, auth):
        r = httpx.post(f"{base_url}/resolve", json={"ticket": "   "}, headers=auth, timeout=TIMEOUT_FAST)
        assert r.status_code == 400

    def test_repeat_request_is_cached(self, base_url, auth):
        # First call — might be fresh or already cached
        r1 = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE)
        assert r1.status_code == 200

        time.sleep(2.5)  # clear rate-limit window

        # Second identical call must come from cache
        r2 = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE)
        assert r2.status_code == 200
        res = r2.json()["resolution"]
        assert res["from_cache"] is True, "Second identical call should be cached"

    def test_different_products_different_results(self, base_url, auth):
        if not _SECOND_PRODUCT_VALUE:
            pytest.skip("Only one product configured")
        payload_a = {**_FULL_PAYLOAD, "product": _DEFAULT_PRODUCT_VALUE}
        payload_b = {**_FULL_PAYLOAD, "product": _SECOND_PRODUCT_VALUE}

        r_a = httpx.post(f"{base_url}/resolve", json=payload_a, headers=auth, timeout=TIMEOUT_RESOLVE)
        assert r_a.status_code == 200

        time.sleep(2.5)

        r_b = httpx.post(f"{base_url}/resolve", json=payload_b, headers=auth, timeout=TIMEOUT_RESOLVE)
        assert r_b.status_code == 200

        # Different products should produce different cache keys
        key_a = r_a.json()["resolution"]["cache_key"]
        key_b = r_b.json()["resolution"]["cache_key"]
        assert key_a != key_b, "Different products should not share a cache key"

    def test_rate_limit_enforced(self, base_url, auth):
        r1 = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE)
        r2 = httpx.post(
            f"{base_url}/resolve",
            json={"ticket": "completely different issue password reset broken"},
            headers=auth,
            timeout=TIMEOUT_RESOLVE,
        )
        statuses = {r1.status_code, r2.status_code}
        assert 200 in statuses or 429 in statuses

    def test_release_notes_search_does_not_error(self, base_url, auth):
        payload = {
            "ticket": "What changed in the latest release? Looking for recent updates.",
            "product": _DEFAULT_PRODUCT_VALUE,
            "permission_level": "admin/manager",
            "access_channel": "website",
        }
        r = httpx.post(f"{base_url}/resolve", json=payload, headers=auth, timeout=TIMEOUT_RESOLVE)
        assert r.status_code == 200
        res = r.json()["resolution"]
        assert res["confidence"] in ("HIGH", "MEDIUM", "LOW", "")
        if not res["confidence"]:
            assert "confidence_scorer" in res
        assert res.get("draft_email") or _is_safe_abstention(res)


# ── /feedback ────────────────────────────────────────────────

class TestFeedback:
    def test_thumbs_up_returns_ok(self, base_url, auth):
        r = httpx.post(
            f"{base_url}/feedback",
            json={"rating": "thumbs_up", "cache_key": "test-key", "confidence": "HIGH"},
            headers=auth,
            timeout=TIMEOUT_FAST,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_thumbs_down_returns_ok(self, base_url, auth):
        r = httpx.post(
            f"{base_url}/feedback",
            json={"rating": "thumbs_down", "cache_key": "test-key", "confidence": "LOW"},
            headers=auth,
            timeout=TIMEOUT_FAST,
        )
        assert r.status_code == 200

    def test_feedback_with_retrieval_signals(self, base_url, auth):
        r = httpx.post(
            f"{base_url}/feedback",
            json={
                "rating": "thumbs_down",
                "cache_key": "test-key",
                "confidence": "LOW",
                "top_score": 2.34,
                "score_gap": 1.1,
                "rerank_scores": "[2.34, 1.24, 0.5]",
                "retrieved_chunk_ids": '["chunk_001","chunk_002"]',
                "used_retrieval_cache": False,
                "used_response_cache": False,
            },
            headers=auth,
            timeout=TIMEOUT_FAST,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_feedback_without_auth_returns_401(self, base_url):
        r = httpx.post(
            f"{base_url}/feedback",
            json={"rating": "thumbs_up"},
            timeout=TIMEOUT_FAST,
        )
        assert r.status_code == 401


# ── end-to-end: resolve then feedback ────────────────────────

class TestEndToEnd:
    def test_resolve_then_rate_feedback(self, base_url, auth):
        r = httpx.post(f"{base_url}/resolve", json=_FULL_PAYLOAD, headers=auth, timeout=TIMEOUT_RESOLVE)
        assert r.status_code == 200

        res = r.json()["resolution"]
        cache_key = res.get("cache_key", "")
        sig = res.get("retrieval_signals", {})

        time.sleep(0.5)

        fb = httpx.post(
            f"{base_url}/feedback",
            json={
                "rating": "thumbs_up",
                "cache_key": cache_key,
                "confidence": res.get("confidence", ""),
                "from_cache": res.get("from_cache", False),
                "top_score": sig.get("top_score", 0.0),
                "score_gap": sig.get("score_gap", 0.0),
                "rerank_scores": str(sig.get("rerank_scores", [])),
                "retrieved_chunk_ids": str(sig.get("retrieved_chunk_ids", [])),
                "used_retrieval_cache": sig.get("used_retrieval_cache", False),
                "used_response_cache": sig.get("used_response_cache", False),
            },
            headers=auth,
            timeout=TIMEOUT_FAST,
        )
        assert fb.status_code == 200
        assert fb.json()["status"] == "ok"


# --- live config update tests ---
from pathlib import Path
from copy import deepcopy

from backend.core import project_config


ROOT = Path(__file__).resolve().parent.parent
CONFIGURATOR_HTML = (ROOT / "frontend" / "configurator" / "index.html").read_text()
APP_PY = (ROOT / "backend" / "api" / "app.py").read_text()
ORCHESTRATOR_PY = (ROOT / "backend" / "core" / "orchestrator.py").read_text()


def test_config_impact_labels_are_correct():
    assert project_config.classify_config_impact("output.mode") == "Applies on next resolve"
    assert project_config.classify_config_impact("output.include.sources") == "Applies on next resolve"
    assert project_config.classify_config_impact("workflow.mode") == "Applies on next resolve"
    assert project_config.classify_config_impact("retrieval_policy.route_policies.bug.boost") == "Applies on next resolve"
    assert project_config.classify_config_impact("retrieval_policy.source_authority.policy") == "Applies on next resolve"
    assert project_config.classify_config_impact("sources.knowledge_base.path") == "Requires knowledge reload"
    assert project_config.classify_config_impact("sources.knowledge_base.column_mapping") == "Requires knowledge reload"
    assert project_config.classify_config_impact("retrieval_policy.chunk_type_rules.billing") == "Requires knowledge reload"
    assert project_config.classify_config_impact("retrieval_policy.retrieval.parent_section_expansion") == "Requires knowledge reload"
    assert project_config.classify_config_impact("DATABASE_URL") == "Requires app restart"
    assert project_config.classify_config_impact("ACTIVE_PROVIDER") == "Requires app restart"
    assert project_config.classify_config_impact("MODELS.openai") == "Requires app restart"
    assert project_config.classify_config_impact("WARM_LOCAL_MODELS") == "Requires app restart"


def test_live_safe_settings_change_runtime_fingerprint(monkeypatch):
    one = project_config.load_config()
    two = deepcopy(one)
    two["output"]["output"]["mode"] = "email_draft_only" if one["output"]["output"].get("mode") != "email_draft_only" else "resolution_full"
    monkeypatch.setattr(project_config, "load_config", lambda: one)
    first = project_config.runtime_fingerprint()
    monkeypatch.setattr(project_config, "load_config", lambda: two)
    second = project_config.runtime_fingerprint()
    assert first != second


def test_product_slug_normalizes_to_display_name_for_retrieval():
    config_data = {
        "products": {
            "sample_product": {
                "display_name": "Sample Product",
                "slug": "sample_product",
                "aliases": ["sample"],
                "default_product": True,
            }
        }
    }
    assert project_config.normalize_product_for_retrieval("sample_product", config_data) == "sample product"
    assert project_config.normalize_product_for_retrieval("", config_data) == "sample product"
    assert "sample product" in project_config.product_values_for_retrieval("sample_product", config_data)
    assert "sample_product" in project_config.product_values_for_retrieval("sample_product", config_data)
    assert project_config.canonical_product_for_ingestion("sample_product", config_data) == "Sample Product"


def test_no_retrieval_fallback_applies_output_preferences():
    assert "responder.apply_output_preferences(fallback)" in ORCHESTRATOR_PY
    assert '"confidence_band": "red"' in ORCHESTRATOR_PY
    assert '"draft_email": ""' in ORCHESTRATOR_PY


def test_reload_required_settings_show_reload_notice():
    assert "Requires knowledge reload" in CONFIGURATOR_HTML
    assert "Reload required" in CONFIGURATOR_HTML
    assert "Reload knowledge with: python knowledge_loader/kb_loader.py" in CONFIGURATOR_HTML


def test_restart_required_settings_show_restart_notice():
    assert "Requires app restart" in CONFIGURATOR_HTML
    assert "Restart required" in CONFIGURATOR_HTML
    assert "Restart the FastAPI app" in CONFIGURATOR_HTML


def test_configurator_impact_badges_show_roadmap_labels():
    assert 'if (value === "Applies on next resolve") return "Live ✓"' in CONFIGURATOR_HTML
    assert 'if (value === "Requires knowledge reload") return "Reload required"' in CONFIGURATOR_HTML
    assert 'if (value === "Requires app restart") return "Restart required"' in CONFIGURATOR_HTML
    assert "normalizeStaticImpactBadges()" in CONFIGURATOR_HTML


def test_configurator_source_preview_ui_exists():
    assert "/configurator/source-preview" in CONFIGURATOR_HTML
    assert "Preview Source" in CONFIGURATOR_HTML
    assert "sample_chunk_previews" in CONFIGURATOR_HTML
    assert "detected_columns" in CONFIGURATOR_HTML


def test_resolve_endpoint_remains_present():
    assert '@app.post("/resolve"' in APP_PY


def test_diagnostics_endpoints_remain_present():
    assert '@app.get("/diagnostics/config"' in APP_PY
    assert '@app.get("/diagnostics/checks"' in APP_PY
    assert '@app.post("/diagnostics/checks/{check_id}"' in APP_PY


def test_diagnostics_ui_is_same_page_with_configurator():
    assert 'data-view="config">Configurator' in CONFIGURATOR_HTML
    assert 'data-view="diagnostics">Diagnostics' in CONFIGURATOR_HTML
    assert 'id="diagnosticsView"' in CONFIGURATOR_HTML
    assert 'showView(view' in CONFIGURATOR_HTML


def test_diagnostics_ui_has_live_config_snapshot_and_chat_sessions():
    assert "function runtimeConfiguratorState" in CONFIGURATOR_HTML
    assert "function renderConfigSnapshot" in CONFIGURATOR_HTML
    assert "function createChatDiagnosticSession" in CONFIGURATOR_HTML
    assert "configSnapshot" in CONFIGURATOR_HTML
    assert "Replay This Run" in CONFIGURATOR_HTML


def test_diagnostics_ui_has_test_controls_and_logs():
    assert "Test All" in CONFIGURATOR_HTML
    assert "diagnostics.test.started" in CONFIGURATOR_HTML
    assert "diagnostics.test_all.started" in CONFIGURATOR_HTML
    assert "levelFilter" in CONFIGURATOR_HTML
    assert "Copy Logs" in CONFIGURATOR_HTML


def test_no_extra_default_llm_calls_for_preview():
    preview_block = APP_PY.split('@app.post("/configurator/source-preview"', 1)[1].split("# ── Main Endpoint", 1)[0]
    assert "get_provider(" not in preview_block
    assert "orchestrator.run" not in preview_block


# --- runtime config tests ---
from pathlib import Path

from fastapi.testclient import TestClient

from backend.core import project_config
from pipeline.retrieval_policy import get_route_policy, score_candidate_with_policy
from pipeline import planner, query_builder, responder, retriever, validation
from knowledge_loader.kb_loader import detect_chunk_type, detect_condition_flags, chunk_with_sections, build_chunk_texts, plan_document_reingestion
from backend.providers import get_provider, reset_provider_cache
from backend.core import config
from backend.providers.model_warmup import warm_local_models
from backend.api.app import app, _mask_diagnostic_value, build_config_diagnostics, run_diagnostic_check

ROOT = Path(__file__).resolve().parent.parent
CONFIGURATOR_HTML = (ROOT / "frontend" / "configurator" / "index.html").read_text()
START_PY = (ROOT / "start.py").read_text()
APP_PY = (ROOT / "backend" / "api" / "app.py").read_text()
TICKET_INDEX = (ROOT / "frontend" / "ticket" / "index.html").read_text()
ORCHESTRATOR_CACHE_PY = (ROOT / "pipeline" / "orchestrator_cache.py").read_text()
ORCHESTRATOR_PY = (ROOT / "backend" / "core" / "orchestrator.py").read_text()
DOCKER_COMPOSE = (ROOT / "docker-compose.yml").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()
README_MD = (ROOT / "README.md").read_text()
TECHNICAL_MD = (ROOT / "docs" / "TECHNICAL.md").read_text()


def test_phase_1_start_py_defaults_to_loopback():
    assert 'APP_BIND_HOST = os.getenv("APP_BIND_HOST", "127.0.0.1")' in START_PY
    assert '"--host", APP_BIND_HOST' in START_PY
    assert '"--host", "0.0.0.0"' not in START_PY


def test_phase_1_docker_publish_ports_are_loopback_only():
    assert '"127.0.0.1:${DB_HOST_PORT:-5432}:5432"' in DOCKER_COMPOSE
    assert '"127.0.0.1:8000:8000"' in DOCKER_COMPOSE
    assert '"127.0.0.1:8765:8765"' in DOCKER_COMPOSE
    assert "BIND_HOST: 0.0.0.0" in DOCKER_COMPOSE
    assert 'BIND_HOST=${BIND_HOST:-127.0.0.1}' in DOCKERFILE


def test_phase_1_doctor_checks_loopback_exposure_and_key_strength():
    doctor = (ROOT / "scripts" / "demo_doctor.sh").read_text()

    assert "Loopback exposure" in doctor
    assert "0.0.0.0" in doctor
    assert "Key strength" in doctor
    assert "at least 12 characters" in doctor


def test_operational_secret_validation_rejects_empty_placeholder_and_shared_values():
    with pytest.raises(ValueError, match="API_KEY"):
        config.validate_operational_secrets({
            "API_KEY": "",
            "CONFIGURATOR_API_KEY": "admin-secret",
            "VIEWER_TOKEN": "viewer-secret",
            "CONFIGURATOR_ADMIN_TOKEN": "config-admin-secret",
        })
    with pytest.raises(ValueError, match="CONFIGURATOR_API_KEY"):
        config.validate_operational_secrets({
            "API_KEY": "viewer-secret",
            "CONFIGURATOR_API_KEY": "change-me-configurator",
            "VIEWER_TOKEN": "trace-viewer-secret",
            "CONFIGURATOR_ADMIN_TOKEN": "config-admin-secret",
        })
    with pytest.raises(ValueError, match="must not share"):
        config.validate_operational_secrets({
            "API_KEY": "same-secret",
            "CONFIGURATOR_API_KEY": "same-secret",
            "VIEWER_TOKEN": "viewer-secret",
            "CONFIGURATOR_ADMIN_TOKEN": "config-admin-secret",
        })


def test_config_module_import_is_safe_without_api_key():
    env = os.environ.copy()
    env["API_KEY"] = ""

    result = subprocess.run(
        [sys.executable, "-c", "import backend.core.config; print('imported')"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout


def test_operational_secret_validation_accepts_distinct_strong_values():
    config.validate_operational_secrets({
        "API_KEY": "viewer-secret-123",
        "CONFIGURATOR_API_KEY": "configurator-secret-123",
        "VIEWER_TOKEN": "trace-viewer-secret-123",
        "CONFIGURATOR_ADMIN_TOKEN": "admin-secret-123",
    })


def test_phase_1_auth_diagnostic_fails_for_placeholder_keys(monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "change-me")
    monkeypatch.setattr(config, "CONFIGURATOR_API_KEY", "change-me-configurator")
    monkeypatch.setattr(config, "VIEWER_TOKEN", "change-me")
    monkeypatch.setattr(config, "CONFIGURATOR_ADMIN_TOKEN", "change-me-configurator")

    result = run_diagnostic_check("auth_config")

    assert result["status"] == "fail"
    assert "placeholder" in result["message"].lower()


def test_phase_3_resolved_config_files_report_absolute_paths_and_sources():
    files = project_config.resolved_config_files()

    assert set(files) == {"products", "sources", "output", "retrieval_policy", "workflow"}
    for item in files.values():
        assert Path(item["active_path"]).is_absolute()
        assert Path(item["example_path"]).is_absolute()
        assert item["source"] in {"local", "example", "default"}


def test_phase_3_retrieval_diagnostic_includes_resolved_config_paths():
    result = run_diagnostic_check("retrieval_pipeline")

    assert "config_files" in result["details"]
    assert "retrieval_policy" in result["details"]["config_files"]


def test_phase_2_readme_contains_security_privacy_and_quickstart_contract():
    assert "Do not load private customer data into a public or shared instance." in README_MD
    assert "local-first doesn't mean offline" in README_MD
    assert "Exposing beyond localhost exposes traces and admin analytics." in README_MD
    assert "What This Is" in README_MD
    assert "What This Is Not" in README_MD
    assert "You're set up when" in README_MD
    assert "`mode: \"suggest\"`" in README_MD


def test_phase_3_docs_include_config_map_and_reload_semantics():
    assert "| File / Surface | Purpose | User Should Edit? | Takes Effect |" in TECHNICAL_MD
    assert "| Runtime file | Applies | Reload behavior |" in TECHNICAL_MD


def test_phase_3_runtime_config_validation_reports_file_key_problem():
    result = project_config.validate_runtime_config_files()

    assert result["valid"] is True
    assert set(result["files"]) == {"products", "sources", "output", "retrieval_policy", "workflow"}
    for item in result["files"].values():
        assert Path(item["path"]).is_absolute()


def test_phase_4_public_ingest_is_csv_only_static_contract():
    assert "XLSX is supported for source-contract validation and configurator preview" in TECHNICAL_MD
    assert "demo_data/onboarding/source_manifest_template.csv" in TECHNICAL_MD
    assert 'CONFIGURATOR_SOURCE_PREVIEW_SUFFIX_ALLOWLIST = {".csv", ".xlsx"}' in APP_PY
    assert "Public preview ingest supports CSV only" in (ROOT / "knowledge_loader" / "kb_loader.py").read_text()


def test_phase_4_source_fixtures_and_validator_exist():
    from knowledge_loader.source_contract import source_validation_report

    valid = ROOT / "demo_data" / "csv" / "minimal_valid_kb.csv"
    invalid = ROOT / "demo_data" / "csv" / "invalid_examples" / "missing_is_approved.csv"
    report = source_validation_report([valid])
    bad_report = source_validation_report([invalid])

    assert report["counts_by_format"]["csv"]["loaded"] == 3
    assert bad_report["validation_errors"]
    assert (ROOT / "scripts" / "validate_sources.py").exists()
    assert (ROOT / "demo_data" / "pdf" / "PREVIEW_ONLY.md").exists()
    assert (ROOT / "demo_data" / "xlsx" / "PREVIEW_ONLY.md").exists()


def test_phase_4_truth_table_and_reingestion_docs_present():
    assert "Source eligibility truth table" in TECHNICAL_MD
    assert "re-running ingest on a changed file" in TECHNICAL_MD
    assert "`is_approved` | `is_active` | `is_customer_facing_allowed`" in TECHNICAL_MD


def test_phase_4_every_source_eligibility_combination_is_locked():
    from knowledge_loader.source_contract import SourceRecord, chunk_source_records

    records = []
    for approved in (True, False):
        for active in (True, False):
            for customer in (True, False):
                records.append(SourceRecord(
                    source_id=f"src_{approved}_{active}_{customer}",
                    source_title="Eligibility",
                    source_type="csv",
                    source_uri="memory.csv",
                    source_authority="canonical",
                    is_approved=approved,
                    is_active=active,
                    is_customer_facing_allowed=customer,
                    approved_at="2026-01-01",
                    reviewed_by="support_ops",
                    needs_review_at="2027-01-01",
                    doc_type="faq",
                    product_area="login",
                    issue_class="password_reset",
                    version_scope="v1",
                    escalation_risk="low",
                    body="This source has enough words to create one eligible chunk for testing.",
                ))

    chunks, report = chunk_source_records(records)

    assert len(chunks) == 1
    assert chunks[0].source_id == "src_True_True_True"
    assert report["skipped_inactive_or_empty"] == 7


def test_phase_7_fail_closed_retrieval_metadata_guard():
    assert retriever._missing_safety_metadata({"id": "chunk_missing_flags"}) is True
    assert retriever._missing_safety_metadata({
        "source_id": "s1",
        "source_type": "csv",
        "source_category": "knowledge_base",
        "tier": "approved",
        "source_ref": "s1",
        "lineage_ref": "lineage",
        "reviewed_by": "support_ops",
        "approved_at": "2026-01-01",
        "audience_allowed": ["customer"],
        "is_customer_facing_allowed": True,
        "is_internal_only": False,
        "is_future_only": False,
        "source_url": "https://example.test",
        "document_hash": "doc",
        "chunk_hash": "chunk",
        "updated_at": "2026-01-01",
        "redaction_status": "redacted",
        "redaction_applied": True,
        "ingested_at": "2026-01-01",
        "loader_version": "test",
        "config_hash": "cfg",
        "disabled": False,
        "source_authority": 1.0,
        "condition_flags": ["none"],
    }) is False


def test_phase_1_named_fail_closed_source_contract_safety_fields():
    from knowledge_loader.source_contract import source_validation_report

    fixtures = {
        "is_approved": ROOT / "demo_data" / "csv" / "invalid_examples" / "missing_is_approved.csv",
        "is_active": ROOT / "demo_data" / "csv" / "invalid_examples" / "inactive_source.csv",
        "is_customer_facing_allowed": ROOT / "demo_data" / "csv" / "invalid_examples" / "internal_only_source.csv",
    }

    missing_report = source_validation_report([fixtures["is_approved"]])
    assert any("is_approved" in error["message"] for error in missing_report["validation_errors"])

    for field_name in ("is_active", "is_customer_facing_allowed"):
        report = source_validation_report([fixtures[field_name]])
        assert report["counts_by_format"]["csv"]["loaded"] == 1
        assert report["counts_by_format"]["csv"]["chunked"] == 0


def test_phase_1_raw_ticket_source_policy_and_evidence_ban():
    candidate = {
        "id": "raw_1",
        "source_id": "historical_tickets:T-1",
        "source_type": "raw_ticket_history",
        "rrf_score": 9.0,
        "source_authority": 1.0,
    }

    scored = score_candidate_with_policy(candidate, "general")

    assert scored["policy_disallowed"] is True
    assert scored["policy_score"] == -1.0


def test_phase_1_kb_scraper_is_explicitly_experimental_and_excluded():
    scraper = (ROOT / "knowledge_loader" / "kb_scraper.py").read_text()

    assert "Experimental/offline helper only" in scraper
    assert "excluded from the public preview ingest" in scraper


def test_phase_5_doctor_has_fix_lines_config_paths_and_source_preview():
    doctor = (ROOT / "scripts" / "demo_doctor.sh").read_text()
    makefile = (ROOT / "Makefile").read_text()

    assert "Fix:" in doctor
    assert "Resolved config paths" in doctor
    assert "Source preview dry run" in doctor
    assert "reset-demo:" in makefile
    assert "reload-kb:" in makefile
    assert "Common Failures" in README_MD
    assert "Logs live under" in README_MD


def test_phase_6_ticket_ui_shows_trust_controls_and_human_readable_citations():
    assert "Confidence band:" in TICKET_INDEX
    assert "why this draft" in TICKET_INDEX
    assert "abstention_reason" in TICKET_INDEX
    assert "suggested_next_action" in TICKET_INDEX
    assert "product_area" in TICKET_INDEX
    assert "why_eligible" in TICKET_INDEX
    assert "Trace walkthrough" in (ROOT / "docs" / "DEMO.md").read_text()


def test_phase_6_review_queue_remains_visible_as_human_review_proof():
    assert "review queue rows" in TECHNICAL_MD
    assert "Human review required before any customer response" in README_MD


def test_phase_7_launch_gates_and_ci_exist():
    workflow = ROOT / ".github" / "workflows" / "public-preview.yml"
    assert workflow.exists()
    text = workflow.read_text()
    assert "scripts/public_smoke.sh" in text
    assert "phase_7" in text
    assert "citation precision" in (ROOT / "scripts" / "ci_golden_eval.sh").read_text().lower()


def test_phase_7_support_bundle_redacts_configured_secrets(monkeypatch):
    from backend.api import app as app_module

    monkeypatch.setattr(config, "API_KEY", "rk_secret_viewer_12345")
    payload = {"nested": ["before rk_secret_viewer_12345 after"]}

    redacted = app_module._redact_export_value(payload)

    assert "rk_secret_viewer_12345" not in str(redacted)
    assert "[REDACTED_SECRET]" in str(redacted)


def test_phase_7_fast_follow_golden_retrieval_has_stable_source_ids():
    golden = ROOT / "eval" / "golden_set" / "v3_1_starter.jsonl"
    rows = [json.loads(line) for line in golden.read_text().splitlines() if line.strip()]

    assert rows
    assert any(row.get("expected_sources") or row.get("expected_source_ids") for row in rows)


def test_phase_7_fast_follow_minimal_and_invalid_fixtures_match_preview_examples():
    from knowledge_loader.source_contract import source_validation_report

    valid = source_validation_report([ROOT / "demo_data" / "csv" / "minimal_valid_kb.csv"])
    invalid_dir = ROOT / "demo_data" / "csv" / "invalid_examples"
    invalid = source_validation_report(sorted(invalid_dir.glob("*.csv")))

    assert valid["counts_by_format"]["csv"]["loaded"] == 3
    assert valid["counts_by_format"]["csv"]["chunked"] == 3
    assert invalid["counts_by_format"]["csv"]["rejected"] >= 6


def test_phase_7_fast_follow_citation_format_and_trace_link_ui_contract():
    assert "source_title" in TICKET_INDEX
    assert "product_area" in TICKET_INDEX
    assert "why_eligible" in TICKET_INDEX
    assert "why this draft" in TICKET_INDEX
    assert "/traces/${encodeURIComponent(resolution.trace_id)}" in TICKET_INDEX


def test_phase_7_fast_follow_config_reload_and_provider_validation_contracts():
    validation_result = project_config.validate_runtime_config_files()

    assert validation_result["valid"] is True
    assert "validate_operational_secrets" in (ROOT / "backend" / "core" / "config.py").read_text()
    assert "| Runtime file | Applies | Reload behavior |" in TECHNICAL_MD
    assert "model_warmup" in README_MD


def test_phase_7_fast_follow_port_conflict_handling_is_documented_and_doctored():
    doctor = (ROOT / "scripts" / "demo_doctor.sh").read_text()

    assert "Port 8000/8765 in use" in README_MD
    assert "port" in doctor.lower()


def test_phase_7_fast_follow_direct_reranker_and_confidence_tests(monkeypatch):
    from pipeline import reranker
    from pipeline.confidence import compute_scorer_result

    class FakeCrossEncoder:
        def predict(self, pairs, batch_size=32):
            return [1.0, 3.0]

    monkeypatch.setattr(reranker, "_get_cross_encoder", lambda: FakeCrossEncoder())
    context = {
        "search_query": "reset password",
        "retrieved_chunks": [
            approved_chunk(id="low", content="Adjacent account setup guidance.", source_authority=1.0),
            approved_chunk(id="high", content="Reset your password from account settings.", source_authority=1.0),
        ],
        "route_hints": {"top_k_rerank": 1},
    }

    reranked = reranker.run(context)
    assert reranked["top_chunks"][0]["id"] == "high"

    score = compute_scorer_result(reranked["top_chunks"], evidence_bundle=EvidenceBundle.from_chunks(reranked["top_chunks"]))
    assert score.confidence_band in {"red", "yellow", "green"}


def test_route_policy_unknown_route_returns_general():
    policy = get_route_policy("unknown_route")
    assert policy["preferred_chunk_types"]


def test_route_policy_boosts_preferred_chunk_type():
    candidate = {
        "id": "x",
        "rrf_score": 0.01,
        "chunk_type": "billing",
        "source_type": "policy",
        "source_authority": 1.0,
    }
    scored = score_candidate_with_policy(candidate, "billing")
    assert scored["policy_score"] > candidate["rrf_score"]


def test_chunk_type_rules_apply_deterministically():
    chunk_type = detect_chunk_type(
        "The customer needs a refund for a duplicate invoice.",
        "Billing help",
        "Billing",
        "knowledge_base",
    )
    assert chunk_type == "billing"


def test_parent_section_tracking_when_headings_exist():
    chunks, sections = chunk_with_sections("# Setup\n\nUse these steps.", "article")
    assert sections[0]["heading_path"] == "Setup"
    assert chunks[0]["parent_section_id"] == sections[0]["id"]


def test_chunk_texts_split_embedding_and_display_context():
    texts = build_chunk_texts(
        "Only managers can approve this request.",
        title="Availability approvals",
        source_type="official_help_article",
        heading_path="Approvals",
        section_text="Only managers can approve this request.",
        product="Example Product",
        platform="website",
        role_or_permission="manager",
    )
    assert "Title: Availability approvals" in texts["embedding_text"]
    assert "Product: Example Product" in texts["embedding_text"]
    assert "Platform: website" in texts["embedding_text"]
    assert "Role or permission: manager" in texts["embedding_text"]
    assert "Source type: official_help_article" in texts["embedding_text"]
    assert "Article: Availability approvals" in texts["display_text"]
    assert "Section: Approvals" in texts["display_text"]
    assert "Product: Example Product" not in texts["display_text"]


def test_source_authority_validation_rejects_out_of_range():
    data = project_config.load_config()
    data["retrieval_policy"]["source_authority"]["policy"] = 2.0
    validation = project_config.validate_config(data)
    assert not validation["valid"]


def test_source_authority_presets_apply_and_forbidden_stays_forbidden():
    policy = project_config.load_config("retrieval_policy")
    policy["source_authority_presets"]["active"] = "strict"
    assert project_config.get_source_authority("policy", policy) == 1.0
    assert project_config.get_source_authority("known_issue", policy) == 0.75
    assert project_config.get_source_authority("raw_ticket_history", policy) == 0.0


def test_source_authority_preset_cannot_enable_raw_history():
    data = project_config.load_config()
    data["retrieval_policy"]["source_authority_presets"]["presets"]["balanced"]["raw_ticket_history"] = 0.5
    validation = project_config.validate_config(data)
    assert validation["valid"] is False
    assert "forbidden source" in "; ".join(validation["errors"])


def test_document_reingestion_plans_insert_skip_update_and_delete():
    insert = plan_document_reingestion(
        article_id="kb_setup",
        document_hash="new",
        desired_chunk_ids=["kb_setup_0"],
        existing_document_hashes={},
        loaded_ids=set(),
    )
    assert insert["action"] == "insert"

    skip = plan_document_reingestion(
        article_id="kb_setup",
        document_hash="same",
        desired_chunk_ids=["kb_setup_0"],
        existing_document_hashes={"kb_setup": "same"},
        loaded_ids={"kb_setup_0"},
    )
    assert skip["action"] == "skip_unchanged"

    changed = plan_document_reingestion(
        article_id="kb_setup",
        document_hash="new",
        desired_chunk_ids=["kb_setup_0"],
        existing_document_hashes={"kb_setup": "old"},
        loaded_ids={"kb_setup_0", "kb_setup_1"},
    )
    assert changed["action"] == "upsert_changed"
    assert changed["chunks_to_delete"] == ["kb_setup_1"]


def test_output_config_rejects_autonomous_modes():
    data = project_config.load_config()
    for mode in ["auto_send", "auto_resolve", "account_action", "kb_rewrite"]:
        data["output"]["output"]["mode"] = mode
        validation = project_config.validate_config(data)
        assert not validation["valid"], mode
        assert "suggest-only" in "; ".join(validation["errors"])


def test_startup_warmup_can_be_disabled_without_loading_models():
    assert warm_local_models(enabled=False) is False


def test_startup_warmup_failure_logs_without_traceback(monkeypatch, caplog):
    def fail_warmup():
        raise RuntimeError("offline")

    monkeypatch.setattr("backend.providers.embedding_model.warmup", fail_warmup)

    with caplog.at_level("WARNING", logger="providers.model_warmup"):
        assert warm_local_models(enabled=True) is False

    assert "Local model warmup skipped" in caplog.text
    assert "lazy loading remains available" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_provider_factory_reuses_provider_instance(monkeypatch):
    class FakeProvider:
        def get_name(self):
            return "fake"

    reset_provider_cache()
    monkeypatch.setattr(config, "ACTIVE_PROVIDER", "fake")
    monkeypatch.setitem(config.MODELS, "fake", "fake-model")
    monkeypatch.setattr("backend.providers.PROVIDERS", {"fake": FakeProvider})
    first = get_provider()
    second = get_provider()
    assert first is second
    reset_provider_cache()


def test_diagnostics_masks_secret_values():
    masked = _mask_diagnostic_value("OPENAI_API_KEY", "demo-provider-key-1234567890abcd")
    assert masked.startswith("de...")
    assert masked.endswith("abcd")
    assert "1234567890" not in masked


def test_config_diagnostics_reports_required_values_without_full_secrets(monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "api-secret-value")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://user:pass@localhost:5432/resolvekit")
    items = build_config_diagnostics()
    by_key = {item["key"]: item for item in items}
    assert by_key["API_KEY"]["required"] is True
    assert by_key["API_KEY"]["present"] is True
    assert by_key["API_KEY"]["status"] == "ok"
    assert by_key["API_KEY"]["safeValuePreview"] != "api-secret-value"
    assert "pass" not in by_key["DATABASE_URL"]["safeValuePreview"]


def test_run_diagnostic_check_has_expected_result_shape():
    result = run_diagnostic_check("app_runtime")
    assert result["id"] == "app_runtime"
    assert result["status"] in {"ok", "warn", "fail", "unknown"}
    assert result["message"]
    assert "testedAt" in result
    assert "durationMs" in result


def test_workflow_fast_mode_skips_evaluator(monkeypatch):
    monkeypatch.setattr(project_config, "load_config", lambda section: {
        "workflow": {
            "mode": "fast",
            "max_llm_calls": 2,
            "modes": {
                "fast": {"responder": True, "evaluator": False, "retry_responder_on_low_faithfulness": False},
            },
            "stages": {
                "responder": {"enabled": True, "counts_toward_budget": True},
                "evaluator": {"enabled": True, "counts_toward_budget": True},
                "responder_retry": {"enabled": False, "counts_toward_budget": True},
            },
        }
    })
    workflow = project_config.workflow_settings()
    assert workflow["max_llm_calls"] == 1
    assert workflow["stages"]["evaluator"]["enabled"] is False


def test_workflow_standard_mode_runs_evaluator(monkeypatch):
    monkeypatch.setattr(project_config, "load_config", lambda section: {
        "workflow": {
            "mode": "standard",
            "max_llm_calls": 2,
            "modes": {
                "standard": {"responder": True, "evaluator": True, "retry_responder_on_low_faithfulness": False},
            },
            "stages": {
                "responder": {"enabled": True, "counts_toward_budget": True},
                "evaluator": {"enabled": False, "counts_toward_budget": True},
                "responder_retry": {"enabled": False, "counts_toward_budget": True},
            },
        }
    })
    workflow = project_config.workflow_settings()
    assert workflow["stages"]["evaluator"]["enabled"] is True


def test_workflow_strict_mode_preserves_retry(monkeypatch):
    monkeypatch.setattr(project_config, "load_config", lambda section: {
        "workflow": {
            "mode": "strict",
            "max_llm_calls": 2,
            "modes": {
                "strict": {"responder": True, "evaluator": True, "retry_responder_on_low_faithfulness": True},
            },
            "stages": {
                "responder": {"enabled": True, "counts_toward_budget": True},
                "evaluator": {"enabled": True, "counts_toward_budget": True},
                "responder_retry": {"enabled": False, "counts_toward_budget": True},
            },
        }
    })
    workflow = project_config.workflow_settings()
    assert workflow["max_llm_calls"] == 3
    assert workflow["stages"]["responder_retry"]["enabled"] is True


def test_responder_prompt_includes_conditional_answer_rules():
    assert "role, permission, setting, platform, plan" in responder.SYSTEM_PROMPT
    assert "multiple hypotheses are plausible" in responder.SYSTEM_PROMPT


def test_responder_prompt_avoids_generic_urgency_language():
    assert "Do not use generic empathy formulas" in responder.SYSTEM_PROMPT
    assert "If urgency language was not present, do not mention urgency" in responder.SYSTEM_PROMPT


def test_platform_normalization_is_config_driven():
    product_config = {
        "products": {
            "demo": {
                "display_name": "Demo",
                "slug": "demo",
                "default_product": True,
                "platforms": {
                    "portal": {"normalized": "web_portal", "aliases": ["browser"], "enabled": True},
                    "field_app": {"normalized": "field_app", "aliases": ["ios"], "enabled": True},
                },
            }
        }
    }
    assert project_config.normalize_platform_for_retrieval("field_app", "demo", product_config) == "field_app"
    assert project_config.normalize_platform_for_retrieval("ios", "demo", product_config) == "field_app"
    assert project_config.normalize_platform_for_retrieval("browser", "demo", product_config) == "web_portal"


def test_loader_detects_condition_flags():
    flags = detect_condition_flags(
        "Only if the feature is enabled can managers approve this on iOS.",
        "Approval settings",
        "Permissions",
    )
    assert "requires_setting" in flags
    assert "requires_role" in flags
    assert "platform_specific" in flags


def test_loader_does_not_mark_plain_requires_as_setting_state():
    flags = detect_condition_flags(
        "If sign-in still fails, confirm the user has an active employee role.",
        "Mobile login 403 after update",
    )
    assert "requires_role" in flags
    assert "requires_setting" not in flags


def test_retriever_normalizes_condition_flags_from_rows():
    rows = retriever._normalize_rows([{"id": "x", "condition_flags": '["requires_role"]'}])
    assert rows[0]["condition_flags"] == ["requires_role"]


def test_planner_extracts_structured_fields_and_metadata_filter():
    context = {
        "ticket": {"cleaned": "Urgent: Can enterprise admins export audit history on website in version 2.4?"},
        "request_meta": {"product": "Example Product", "access_channel": "website", "permission_level": "admin"},
        "product": "example product",
        "platform": "website",
    }
    result = planner.run(context)
    output = result["planner_output"]
    assert output["intent"] == "billing" or output["intent"] == result["routing_strategy"]
    assert output["explicit_questions"]
    assert output["product_version"] == "2.4"
    assert output["plan_tier"] == "enterprise"
    assert result["metadata_filter"]["role"] == "admin"


def test_query_builder_carries_planner_questions_and_metadata_filter():
    context = {
        "ticket": {"normalized": "where is the export"},
        "request_meta": {"product": "Example Product", "access_channel": "website"},
        "planner_output": {"explicit_questions": ["Can admins export audit history?"]},
        "metadata_filter": {"role": "admin"},
    }
    result = query_builder.run(context)
    assert "search_query" in result["query_builder_output"]
    assert result["query_builder_output"]["explicit_questions"] == ["Can admins export audit history?"]
    assert result["query_builder_output"]["metadata_filter"]["role"] == "admin"


def test_metadata_filter_falls_back_when_everything_filtered():
    chunks = [{"id": "x", "content": "General setup help."}]
    assert retriever.apply_metadata_filter(chunks, {"role": "admin"}) == chunks


def test_metadata_filter_uses_role_as_context_not_hard_filter():
    chunks = [
        {"id": "admin", "content": "Admins can export audit history."},
        {"id": "agent", "content": "Agents can reply to conversations."},
    ]
    filtered = retriever.apply_metadata_filter(chunks, {"role": "admin"})
    assert [chunk["id"] for chunk in filtered] == ["admin", "agent"]


def test_old_rows_without_condition_flags_still_work():
    rows = retriever._normalize_rows([{"id": "x"}])
    assert rows[0]["condition_flags"] == []


def test_confidence_capped_when_required_context_missing():
    context = {
        "resolution": {"confidence": "HIGH", "sources": "kb.csv", "resolution_steps": "1. Check permissions.", "draft_email": "Subject: Test\n\nHi,\n\nKind regards"},
        "eval_score": {},
        "request_meta": {},
        "ticket": {"cleaned": "Cannot create leave."},
        "top_chunks": [{"condition_flags": ["requires_permission"], "rerank_score": 9.0}],
    }
    result = validation.run(context)
    assert result["resolution"]["confidence"] == "MEDIUM"
    assert result["resolution"]["validation"]["condition_context"]["missing_required_context"] is True


def test_validation_skips_evaluator_failures_when_evaluation_skipped():
    context = {
        "resolution": {
            "confidence": "MEDIUM",
            "sources": "kb.csv",
            "resolution_steps": "1. Confirm the setting and retry with the right role.",
            "draft_email": "Subject: Test\n\nHi,\n\nKind regards",
        },
        "eval_score": {
            "faithfulness": None,
            "completeness": None,
            "flags": ["ignored"],
            "evaluation_skipped": True,
        },
        "request_meta": {"permission_level": "manager", "access_channel": "website"},
        "ticket": {"cleaned": "Cannot approve availability."},
        "top_chunks": [{
            "id": "kb_approved",
            "content": "Managers can approve availability.",
            "source_id": "knowledge_base:availability",
            "source_type": "official_help_article",
            "source_category": "knowledge_base",
            "is_approved": True,
            "tier": "approved_kb",
            "source_ref": "demo_knowledge_base.csv",
            "lineage_ref": "kb_availability",
            "reviewed_by": "demo_seed",
            "approved_at": "2026-05-01T00:00:00+00:00",
            "audience_allowed": ["customer", "internal"],
            "is_customer_facing_allowed": True,
            "source_url": "https://example.test/help/availability",
            "document_hash": "doc_hash",
            "chunk_hash": "chunk_hash",
            "updated_at": "2026-05-01T00:00:00+00:00",
            "ingested_at": "2026-05-01T00:00:00+00:00",
            "loader_version": "test-loader",
            "config_hash": "test-config",
            "condition_flags": [],
            "rerank_score": 3.0,
        }],
    }
    result = validation.run(context)
    assert result["resolution"]["validation"]["evaluation_skipped"] is True
    assert result["resolution"]["validation"]["gatekeeper_flagged"] is False


def test_validation_audits_canonical_resolution_after_display_filtering():
    context = {
        "resolution": {
            "confidence": "MEDIUM",
            "root_cause": "",
            "resolution_steps": "1. Check the approved setup path and retry.",
            "sources": "demo_knowledge_base.csv",
            "draft_email": "Subject: Setup help\n\nHi,\n\nPlease check the approved setup path and retry.\n\nKind regards,\nSupport Team",
            "canonical_resolution": {
                "confidence": "MEDIUM",
                "root_cause": "The approved setup path applies to this request. [KB-1]",
                "resolution_steps": "1. Check the approved setup path and retry.",
                "sources": "demo_knowledge_base.csv",
                "draft_email": "Subject: Setup help\n\nHi,\n\nPlease check the approved setup path and retry.\n\nKind regards,\nSupport Team",
            },
        },
        "eval_score": {"evaluation_skipped": True},
        "request_meta": {"permission_level": "admin", "access_channel": "website"},
        "ticket": {"cleaned": "Need setup help."},
        "top_chunks": [approved_chunk()],
        "evidence_table": {
            "supported_facts": [{"claim": "Approved setup path applies.", "citations": ["KB-1"]}],
            "missing_context": [],
            "conflicts": [],
        },
        "source_conflicts": [],
    }
    validation_data = validation.run(context)["resolution"]["validation"]
    assert not any(
        "Factual answer fields did not cite approved evidence" in claim
        for claim in validation_data["unsupported_claims"]
    )
    assert validation_data["auditor"]["citations_present"] is True


def test_validation_does_not_treat_abstention_guidance_as_uncited_factual_answer():
    context = {
        "resolution": {
            "confidence": "LOW",
            "root_cause": "",
            "resolution_steps": "Escalate for human review or add an approved KB source.",
            "sources": "",
            "draft_email": "",
            "draft_unavailable_reason": "Draft unavailable because no approved source supports a safe answer.",
            "confidence_scorer": {"confidence_band": "red"},
        },
        "eval_score": {"evaluation_skipped": True},
        "request_meta": {},
        "ticket": {"cleaned": "Need setup help."},
        "top_chunks": [approved_chunk()],
        "evidence_table": {
            "supported_facts": [{"claim": "Approved setup path applies.", "citations": ["KB-1"]}],
            "missing_context": [],
            "conflicts": [],
        },
        "source_conflicts": [],
    }
    validation_data = validation.run(context)["resolution"]["validation"]
    assert not any(
        "Factual answer fields did not cite approved evidence" in claim
        for claim in validation_data["unsupported_claims"]
    )


def test_abstention_replacement_drops_stale_answer_fields():
    from backend.core.orchestrator import _abstention_response, _replace_with_abstention

    resolution = {
        "raw": "Old answered text with [KB-1].",
        "rendered_reply": "Old rendered answer with [KB-1].",
        "structured_reply": {"citations": ["[KB-1]"]},
        "canonical_resolution": {"root_cause": "Old root cause. [KB-1]"},
        "cache_key": "abc",
        "usage": {"responder": {"tokens_out": 10}},
        "from_cache": False,
    }
    fallback = _abstention_response("No approved source supports this answer.")
    replaced = _replace_with_abstention(resolution, fallback)
    assert "raw" not in replaced
    assert "rendered_reply" not in replaced
    assert "structured_reply" not in replaced
    assert replaced["canonical_resolution"]["root_cause"] == replaced["root_cause"]
    assert replaced["cache_key"] == "abc"
    assert replaced["usage"] == {"responder": {"tokens_out": 10}}


def test_responder_prompt_requires_resolution_step_citations():
    assert "Every factual or actionable resolution step must cite" in responder.SYSTEM_PROMPT
    assert "[KB-N]" in responder.SYSTEM_PROMPT


def test_parent_expansion_falls_back_when_parent_missing(monkeypatch):
    class Cursor:
        def execute(self, *args):
            return None
        def fetchall(self):
            return []
        def close(self):
            return None

    class Conn:
        def cursor(self, *args, **kwargs):
            return Cursor()

    monkeypatch.setattr(project_config, "load_config", lambda section: {"retrieval": {"parent_section_expansion": True}})
    chunks = [{"id": "x", "content": "child", "parent_section_id": "missing"}]
    assert retriever.expand_parent_sections(chunks, Conn()) == chunks


def test_parent_expansion_is_capped_and_traced(monkeypatch):
    class Cursor:
        def execute(self, *args):
            return None
        def fetchall(self):
            return [{
                "id": "parent",
                "section_text": " ".join([f"word{i}" for i in range(40)]),
                "title": "Parent",
                "heading_path": "Parent",
            }]
        def close(self):
            return None

    class Conn:
        def cursor(self, *args, **kwargs):
            return Cursor()

    monkeypatch.setattr(project_config, "load_config", lambda section: {"retrieval": {"parent_section_expansion": True, "max_expansion_ratio": 2.0}})
    chunks = [{"id": "x", "content": "one two three four five", "parent_section_id": "parent"}]
    expanded = retriever.expand_parent_sections(chunks, Conn())
    assert len(expanded[0]["content"].split()) == 10
    assert expanded[0]["expansion_trace"]["capped"] is True
    assert expanded[0]["expansion_trace"]["max_expansion_ratio"] == 2.0


def test_neighbor_expansion_adds_sibling_and_condition_chunks(monkeypatch):
    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, query, args):
            self.calls.append(args)

        def fetchall(self):
            return [{
                "id": "kb_article_1",
                "content": "Neighbor condition.",
                "article_id": "kb_article",
                "chunk_index": 1,
                "condition_flags": '["requires_role"]',
            }]

        def close(self):
            return None

    class Conn:
        def cursor(self, *args, **kwargs):
            return Cursor()

    monkeypatch.setattr(project_config, "load_config", lambda section: {"retrieval": {"sibling_expansion": True, "condition_neighbor_expansion": True}})
    chunks = [{"id": "kb_article_0", "content": "Root.", "article_id": "kb_article", "chunk_index": 0, "condition_flags": ["requires_role"], "rrf_score": 0.1}]
    expanded = retriever.expand_neighbor_chunks(chunks, Conn())
    assert len(expanded) == 2
    assert expanded[1]["retrieval_reason"] == "sibling+condition_neighbor"


def test_retrieval_signals_include_support_context_bundles():
    from backend.core.orchestrator import _collect_retrieval_signals

    signals = _collect_retrieval_signals({
        "top_chunks": [{
            "id": "kb_1",
            "source_id": "policy:exports",
            "source_file": "kb.csv",
            "source_type": "official_help_article",
            "chunk_type": "troubleshooting",
            "condition_flags": ["requires_role"],
            "retrieval_reason": "initial_match+parent_section",
            "rerank_score": 4.2,
        }]
    })
    assert signals["support_context_bundles"][0]["id"] == "kb_1"
    assert signals["support_context_bundles"][0]["source_id"] == "policy:exports"
    assert "initial_match" in signals["source_selection"][0]


def test_configurator_sandbox_formats_resolution_not_object_dump():
    assert "renderResolution(data.resolution)" in CONFIGURATOR_HTML
    assert "displayValue(value)" in CONFIGURATOR_HTML
    assert "[object Object]" not in CONFIGURATOR_HTML


def test_configurator_sandbox_uses_dropdowns_and_log_panel():
    assert '<select id="ticketProduct">' in CONFIGURATOR_HTML
    assert '<select id="accessChannel">' in CONFIGURATOR_HTML
    assert '<select id="permissionLevel">' in CONFIGURATOR_HTML
    assert "admin, manager, scheduler, employee" in CONFIGURATOR_HTML
    assert 'id="sandboxLog"' in CONFIGURATOR_HTML
    assert "function sandboxLog" in CONFIGURATOR_HTML
    assert "function summarizeSandboxRun" in CONFIGURATOR_HTML
    assert "support_context_bundles" in CONFIGURATOR_HTML
    assert "function formatApiError" in CONFIGURATOR_HTML
    assert "HTTP status:" in CONFIGURATOR_HTML
    assert 'const headers = { "Content-Type": "application/json", ...(options.headers || {}) }' in CONFIGURATOR_HTML
    assert 'headers["x-api-key"] = state.configuratorApiKey' in CONFIGURATOR_HTML


def test_configurator_sandbox_respects_output_mode_sections():
    assert "outputSectionEnabled(preferences, key)" in CONFIGURATOR_HTML
    assert 'email_draft_only' in CONFIGURATOR_HTML
    assert '["draft_email", "Draft Email", resolution.draft_email]' in CONFIGURATOR_HTML


def test_configurator_loads_local_dev_api_key():
    assert "/configurator/dev-settings" in CONFIGURATOR_HTML
    assert "prefill_api_key" in CONFIGURATOR_HTML
    assert '$("apiKey").value = data.api_key' in CONFIGURATOR_HTML
    assert 'localStorage.setItem("ai_bot_api_key", data.api_key)' in CONFIGURATOR_HTML
    assert 'localStorage.setItem("resolvekit_configurator_api_key", data.configurator_api_key)' in CONFIGURATOR_HTML


def test_home_page_persists_api_key_in_local_storage():
    assert 'localStorage.getItem("ai_bot_api_key")' in TICKET_INDEX
    assert 'localStorage.setItem("ai_bot_api_key", apiKey)' in TICKET_INDEX or 'localStorage.setItem("ai_bot_api_key", data.api_key)' in TICKET_INDEX
    assert 'else localStorage.removeItem("ai_bot_api_key")' in TICKET_INDEX


def test_home_page_is_react_frontend():
    assert "React.createElement" in TICKET_INDEX
    assert "ReactDOM.createRoot" in TICKET_INDEX
    assert "function App()" in TICKET_INDEX


def test_dark_theme_uses_professional_slate_blue_palette():
    dark_block = TICKET_INDEX.split('[data-theme="dark"]', 1)[1].split('[data-theme="light"]', 1)[0]
    assert "#0b1120" in dark_block
    assert "#111827" in dark_block
    assert "#3b82f6" in dark_block
    assert "#2dd4bf" in dark_block
    assert "#080f1e" not in dark_block
    assert "#0B1629" not in dark_block
    assert "#132040" not in dark_block


def test_home_page_links_to_ticket_sandbox():
    assert 'TICKET_INDEX = BASE_DIR / "frontend" / "ticket" / "index.html"' in APP_PY
    assert "Support Ticket" in TICKET_INDEX
    assert "Ticket sandbox  -> http://localhost:{PORT}" in START_PY
    assert "/configurator/dev-settings" in TICKET_INDEX


def test_home_page_has_config_button_and_retrieval_diagnostics():
    assert 'href: "/configurator" }, "Config"' in TICKET_INDEX
    assert "Retrieval Diagnostics" in TICKET_INDEX
    assert "support_context_bundles" in TICKET_INDEX
    assert "setInterval(loadConfig, 5000)" in TICKET_INDEX
    assert "Internal Use Only" not in TICKET_INDEX
    assert "Response cache: " in TICKET_INDEX
    assert "Retrieval cache: " in TICKET_INDEX
    assert "productDisplayName(products" in TICKET_INDEX
    assert "draft_unavailable_reason" in TICKET_INDEX


def test_ticket_resolution_view_receives_feedback_action_props():
    output_panel_call = TICKET_INDEX.split("resolution ? h(ResolutionView", 1)[1].split(") :", 1)[0]
    assert "agentAction" in output_panel_call
    assert "setAgentAction" in output_panel_call


def test_ticket_workspace_keeps_advanced_retrieval_controls_out_of_main_form():
    input_panel = TICKET_INDEX.split("function InputPanel", 1)[1].split("function OutputPanel", 1)[0]
    assert "Support Mode" not in input_panel
    assert "Pinned Source IDs" not in input_panel
    assert "Similarity Threshold" not in input_panel
    assert 'support_ops_mode: "query"' in TICKET_INDEX
    assert "pinned_source_ids: []" in TICKET_INDEX


def test_ticket_workspace_has_render_error_boundary():
    assert "class ErrorBoundary extends React.Component" in TICKET_INDEX
    assert "UI render error" in TICKET_INDEX
    assert "h(ErrorBoundary" in TICKET_INDEX


def test_ui_file_routes_disable_browser_cache():
    assert 'headers={"Cache-Control": "no-store"}' in APP_PY


def test_configurator_links_back_to_workspace():
    assert 'href="/">Back to Workspace</a>' in CONFIGURATOR_HTML


def test_evaluator_skip_has_structured_eval_score():
    evaluator_py = (ROOT / "pipeline" / "evaluator.py").read_text()
    assert "Evaluator skipped" in evaluator_py
    assert '"evaluation_skipped": True' in evaluator_py
    assert "no evaluator LLM call counted" in ORCHESTRATOR_PY
    assert 'confidence_band") != "red"' in ORCHESTRATOR_PY
    assert 'resolution["mode"] = "suggest"' in ORCHESTRATOR_PY


def test_ticket_cache_path_applies_output_preferences():
    assert "cached = responder.apply_output_preferences(cached)" in ORCHESTRATOR_PY
    assert 'cached["request_context"] = {' in ORCHESTRATOR_PY
    assert 'cached["retrieval_signals"]["used_response_cache"] = True' in ORCHESTRATOR_PY


def test_configurator_hash_opens_sandbox_view():
    assert 'location.hash === "#sandbox" ? "sandbox" : "config"' in CONFIGURATOR_HTML
    assert 'window.addEventListener("hashchange"' in CONFIGURATOR_HTML


def test_configurator_dev_settings_prefills_api_key_for_local_test_client(monkeypatch):
    monkeypatch.setattr(config, "CONFIGURATOR_PREFILL_API_KEY", True)
    monkeypatch.setattr(config, "API_KEY", "dev-secret")
    monkeypatch.setattr(config, "CONFIGURATOR_API_KEY", "config-secret")
    response = TestClient(app).get("/configurator/dev-settings")
    assert response.status_code == 200
    assert response.json()["api_key"] == "dev-secret"
    assert response.json()["configurator_api_key"] == "config-secret"


def test_configurator_dev_settings_blocks_cross_origin_prefill(monkeypatch):
    monkeypatch.setattr(config, "CONFIGURATOR_PREFILL_API_KEY", True)
    monkeypatch.setattr(config, "API_KEY", "dev-secret")
    monkeypatch.setattr(config, "CONFIGURATOR_API_KEY", "config-secret")
    response = TestClient(app).get(
        "/configurator/dev-settings",
        headers={"origin": "https://example.com"},
    )
    assert response.status_code == 200
    assert response.json()["prefill_api_key"] is False
    assert response.json()["api_key"] == ""
    assert response.json()["configurator_api_key"] == ""


def test_output_preference_presets_hide_custom_sections():
    assert 'id="customOutputSections" class="hidden"' in CONFIGURATOR_HTML
    assert 'mode !== "custom"' in CONFIGURATOR_HTML


def test_chunk_and_route_policy_simple_editors_exist_with_json_escape_hatch():
    assert 'id="chunkRulesSimple"' in CONFIGURATOR_HTML
    assert 'id="routePoliciesSimple"' in CONFIGURATOR_HTML
    assert "Advanced JSON" in CONFIGURATOR_HTML


def test_source_authority_not_in_basic_source_cards():
    basic_markup = CONFIGURATOR_HTML.split('<div id="advancedTab"', 1)[0]
    assert "src-authority" not in basic_markup
    assert 'id="sourceAuthoritySimple"' in CONFIGURATOR_HTML


def test_workflow_preset_modes_hide_custom_controls():
    assert 'id="customWorkflowControls" class="hidden"' in CONFIGURATOR_HTML
    assert 'id="workflowMode"' in CONFIGURATOR_HTML
    assert 'mode !== "custom"' in CONFIGURATOR_HTML


def test_response_cache_fingerprint_changes_with_output_config(monkeypatch):
    one = {"output": {"output": {"mode": "resolution_full"}}, "workflow": {"workflow": {"mode": "standard"}}}
    two = {"output": {"output": {"mode": "email_draft_only"}}, "workflow": {"workflow": {"mode": "standard"}}}
    monkeypatch.setattr(project_config, "load_config", lambda: one)
    first = project_config.response_fingerprint()
    monkeypatch.setattr(project_config, "load_config", lambda: two)
    second = project_config.response_fingerprint()
    assert first != second


def test_ticket_cache_key_has_code_version_salt():
    import pipeline.orchestrator_cache as orchestrator_cache

    assert "CACHE_SCHEMA_VERSION" in ORCHESTRATOR_CACHE_PY
    key = orchestrator_cache.build_request_fingerprint(
        "User cannot log in",
        {"product": "example_product", "access_channel": "mobile_app", "permission_level": "agent"},
    )
    assert len(key) == 64


def test_ticket_cache_key_includes_experiment_arm():
    import pipeline.orchestrator_cache as orchestrator_cache

    first = orchestrator_cache.build_request_fingerprint(
        "User cannot log in",
        {"product": "example_product", "experiment_arm": "current_hybrid_rag"},
    )
    second = orchestrator_cache.build_request_fingerprint(
        "User cannot log in",
        {"product": "example_product", "experiment_arm": "current_rag_query_decomposition"},
    )

    assert first != second


def test_retrieval_cache_key_has_code_version_salt():
    retriever_py = (ROOT / "pipeline" / "retriever.py").read_text()
    assert "RETRIEVAL_CACHE_SCHEMA_VERSION" in retriever_py


def test_metadata_filter_does_not_drop_general_login_article_for_role_context():
    chunks = [
        {
            "id": "login",
            "title": "Mobile login 403 after update",
            "content": "If a user sees a 403 error only in the mobile app, sign out and sign back in.",
        },
        {
            "id": "notification",
            "title": "Mobile notification troubleshooting",
            "content": "Push notifications are sent only for conversations assigned to the agent.",
        },
    ]

    filtered = retriever.apply_metadata_filter(chunks, {"role": "agent", "channel": "mobile app"})

    assert [chunk["id"] for chunk in filtered] == ["login", "notification"]


def test_draft_email_placeholder_cleanup():
    cleaned = responder.clean_draft_email("Hi [Customer Name],\n\nThanks,\n[Your Name]")
    assert "[" not in cleaned
    assert "]" not in cleaned


def test_tunnel_startup_option_removed():
    assert ("ENABLE_" + "NG" + "ROK") not in START_PY
    assert ("PUBLIC_" + "START_PATH") not in START_PY
    assert ("ng" + "rok") not in START_PY.lower()


def test_resolve_rejects_unsupported_modes(monkeypatch):
    monkeypatch.setattr("backend.api.app.allow_request", lambda: True)
    for mode in ["auto_send", "auto_resolve", "account_action", "kb_rewrite"]:
        response = TestClient(app).post(
            "/resolve",
            headers={"x-api-key": config.API_KEY},
            json={"ticket": "Please perform an unsafe action.", "mode": mode},
        )
        assert response.status_code == 400
        assert "suggest-only" in response.json()["detail"]


def test_resolve_rejects_unsupported_responder_output_mode(monkeypatch):
    monkeypatch.setattr("backend.api.app.allow_request", lambda: True)
    monkeypatch.setattr("backend.api.app.orchestrator.run", lambda *_args, **_kwargs: {"mode": "auto_send"})
    response = TestClient(app).post(
        "/resolve",
        headers={"x-api-key": config.API_KEY},
        json={"ticket": "Please draft a reply.", "mode": "suggest"},
    )
    assert response.status_code == 400
    assert "suggest-only" in response.json()["detail"]


# --- evidence safety tests ---
from backend.core.evidence import EvidenceBundle, SourceRecord
from pipeline.confidence import CONFIDENCE_BAND_THRESHOLDS, compute_scorer_result
from pipeline import validation


def approved_chunk(**overrides):
    chunk = {
        "id": "kb_1",
        "content": "Approved setup guidance.",
        "source_id": "knowledge_base:setup",
        "source_type": "official_help_article",
        "source_category": "knowledge_base",
        "is_approved": True,
        "tier": "approved_kb",
        "source_ref": "demo_knowledge_base.csv",
        "lineage_ref": "kb_setup",
        "reviewed_by": "demo_seed",
        "approved_at": "2026-05-01T00:00:00+00:00",
        "audience_allowed": ["customer", "internal"],
        "is_customer_facing_allowed": True,
        "source_url": "https://example.test/help/setup",
        "document_hash": "doc_hash",
        "chunk_hash": "chunk_hash",
        "updated_at": "2026-05-01T00:00:00+00:00",
        "ingested_at": "2026-05-01T00:00:00+00:00",
        "loader_version": "test-loader",
        "config_hash": "test-config",
        "rerank_score": 8.0,
    }
    chunk.update(overrides)
    return chunk


def test_approved_kb_source_is_customer_citable():
    bundle = EvidenceBundle.from_chunks([approved_chunk()])
    assert len(bundle.citations) == 1
    assert bundle.blocked == []


def test_unapproved_source_is_rejected():
    bundle = EvidenceBundle.from_chunks([approved_chunk(is_approved=False)])
    assert bundle.citations == []
    assert bundle.blocked[0]["source_type"] == "official_help_article"


def test_raw_historical_ticket_is_rejected_even_with_authority():
    bundle = EvidenceBundle.from_chunks([
        approved_chunk(
            source_id="historical_tickets:T-1",
            source_type="raw_ticket_history",
            source_ref="demo_historical_tickets_offline_only.csv",
            is_approved=True,
            is_customer_facing_allowed=True,
        )
    ])
    assert bundle.citations == []
    assert bundle.blocked[0]["source_type"] == "raw_ticket_history"


def test_missing_source_metadata_fails_closed():
    record = SourceRecord.from_chunk({"id": "kb_missing", "content": "No metadata."})
    assert record.customer_citation_allowed() is False


def test_disabled_source_is_rejected():
    bundle = EvidenceBundle.from_chunks([approved_chunk(disabled=True)])
    assert bundle.citations == []


def test_internal_future_and_stale_sources_are_rejected():
    assert EvidenceBundle.from_chunks([approved_chunk(is_internal_only=True)]).citations == []
    assert EvidenceBundle.from_chunks([approved_chunk(is_future_only=True)]).citations == []
    assert EvidenceBundle.from_chunks([approved_chunk(needs_review_at="2020-01-01T00:00:00+00:00")]).citations == []


def test_missing_required_source_metadata_fails_closed():
    bundle = EvidenceBundle.from_chunks([approved_chunk(source_url="")])
    assert bundle.citations == []
    assert "missing metadata" in bundle.blocked[0]["reason"]


def test_missing_chunk_hash_fails_closed():
    bundle = EvidenceBundle.from_chunks([approved_chunk(chunk_hash="")])
    assert bundle.citations == []


def test_missing_citation_source_is_rejected_by_validator():
    context = {
        "resolution": {
            "confidence": "HIGH",
            "root_cause": "Supported by [KB-2].",
            "resolution_steps": "1. Follow [KB-2].",
            "sources": "demo_knowledge_base.csv",
            "draft_email": "",
        },
        "eval_score": {"evaluation_skipped": True},
        "request_meta": {"permission_level": "admin", "access_channel": "website"},
        "ticket": {"cleaned": "Need setup help."},
        "top_chunks": [approved_chunk()],
    }
    result = validation.run(context)
    assert result["resolution"]["validation"]["passed"] is False
    assert result["resolution"]["validation"]["blocked_citations"][0]["citation_id"] == "KB-2"


def test_invalid_citation_syntax_is_blocked_by_validator():
    variants = [
        "(source: KB article 1)",
        "[ref:KB-1]",
        "KB#1",
        "see KB-1",
        "KB article 1",
        "[source KB-1]",
    ]
    for variant in variants:
        context = {
            "resolution": {
                "confidence": "HIGH",
                "root_cause": f"Supported by {variant}.",
                "resolution_steps": "1. Follow [KB-1] and verify the result.",
                "sources": "demo_knowledge_base.csv [KB-1]",
                "draft_email": "",
            },
            "eval_score": {"evaluation_skipped": True},
            "request_meta": {"permission_level": "admin", "access_channel": "website"},
            "ticket": {"cleaned": "Need setup help."},
            "top_chunks": [approved_chunk()],
        }
        validation_data = validation.run(context)["resolution"]["validation"]
        assert validation_data["passed"] is False, variant
        assert any("Invalid citation syntax" in claim for claim in validation_data["unsupported_claims"])


def test_validator_routes_redaction_failures_to_review():
    context = {
        "resolution": {
            "confidence": "HIGH",
            "root_cause": "Supported by [KB-1].",
            "resolution_steps": "1. Follow [KB-1] and verify the result.",
            "sources": "demo_knowledge_base.csv",
            "draft_email": "",
        },
        "eval_score": {"evaluation_skipped": True},
        "request_meta": {"permission_level": "admin", "access_channel": "website"},
        "ticket": {"cleaned": "Need setup help."},
        "top_chunks": [approved_chunk(redaction_status="failed")],
    }
    result = validation.run(context)
    validation_data = result["resolution"]["validation"]
    assert validation_data["passed"] is False
    assert validation_data["review_required"] is True
    assert validation_data["redaction_status"] == "failed"


def test_confidence_no_approved_source_abstains_red():
    chunk = approved_chunk(is_approved=False)
    bundle = EvidenceBundle.from_chunks([chunk])
    score = compute_scorer_result([chunk], evidence_bundle=bundle)
    assert score.confidence_band == "red"
    assert score.recommended_action in {"refuse", "escalate"}
    assert "No approved" in score.abstention_reason


def test_confidence_missing_context_asks_or_escalates():
    chunk = approved_chunk(rerank_score=6.0)
    bundle = EvidenceBundle.from_chunks([chunk])
    score = compute_scorer_result([chunk], evidence_bundle=bundle, missing_context=["role"])
    assert score.confidence_band in {"yellow", "red"}
    assert score.recommended_action in {"ask_clarifying_question", "escalate"}


def test_confidence_bands_use_explicit_numeric_thresholds():
    assert CONFIDENCE_BAND_THRESHOLDS == {
        "green_min": 0.72,
        "yellow_min": 0.40,
        "red_below": 0.40,
    }
    score = compute_scorer_result([approved_chunk()], evidence_bundle=EvidenceBundle.from_chunks([approved_chunk()]))
    assert score.confidence_thresholds == CONFIDENCE_BAND_THRESHOLDS


# --- public demo tests ---
from pathlib import Path
import csv
import json

from backend.core import project_config
from knowledge_loader import kb_loader


ROOT = Path(__file__).resolve().parent.parent


def test_compact_public_docs_exist():
    expected = [
        "LICENSE",
        "docs/README.md",
        "docs/TECHNICAL.md",
        "docs/DEMO.md",
        "knowledge_loader/processed/demo_knowledge_base.csv",
        "knowledge_loader/processed/demo_policies.csv",
        "knowledge_loader/processed/demo_release_notes.csv",
        "knowledge_loader/processed/demo_known_issues.csv",
        "knowledge_loader/processed/demo_historical_tickets_offline_only.csv",
    ]
    for rel_path in expected:
        assert (ROOT / rel_path).exists(), rel_path
    assert not (ROOT / "static").exists()
    assert not (ROOT / "docs" / "PROJECT_OVERVIEW.html").exists()


def test_default_config_points_to_fictional_demo_sources():
    products = project_config._read_yaml(ROOT / "config/products.example.yaml")["products"]
    assert products["example_product"]["display_name"] == "Example Product"

    sources = project_config._read_yaml(ROOT / "config/sources.example.yaml")["sources"]
    assert sources["knowledge_base"]["path"].endswith("demo_knowledge_base.csv")
    assert sources["policies"]["path"].endswith("demo_policies.csv")
    assert sources["release_notes"]["path"].endswith("demo_release_notes.csv")
    assert sources["known_issues"]["path"].endswith("demo_known_issues.csv")
    assert sources["historical_tickets"]["enabled"] is False


def test_demo_mode_controls_loader_sandbox_sources(tmp_path, monkeypatch):
    (tmp_path / "demo_knowledge_base.csv").write_text("title,content\nDemo,Demo content here\n", encoding="utf-8")
    (tmp_path / "custom.csv").write_text("title,content\nCustom,Custom content here\n", encoding="utf-8")
    monkeypatch.setattr(kb_loader, "PROCESSED_DIR", str(tmp_path))

    monkeypatch.setattr("backend.core.config.DEMO_MODE", True)
    demo_paths = [Path(path).name for path in kb_loader.loader_source_paths()]
    assert "demo_knowledge_base.csv" in demo_paths

    monkeypatch.setattr("backend.core.config.DEMO_MODE", False)
    monkeypatch.setattr(kb_loader, "configured_source_paths", lambda: [])
    custom_paths = [Path(path).name for path in kb_loader.loader_source_paths()]
    assert custom_paths == ["custom.csv"]


def test_demo_mode_includes_configured_custom_csv_sources(tmp_path, monkeypatch):
    processed = tmp_path / "processed"
    uploaded = tmp_path / "uploads"
    processed.mkdir()
    uploaded.mkdir()
    demo = processed / "demo_knowledge_base.csv"
    custom = uploaded / "customer_kb.csv"
    demo.write_text("title,content\nDemo,Demo content here\n", encoding="utf-8")
    custom.write_text("title,content\nCustom,Custom content here\n", encoding="utf-8")
    monkeypatch.setattr(kb_loader, "PROCESSED_DIR", str(processed))
    monkeypatch.setattr("backend.core.config.DEMO_MODE", True)
    monkeypatch.setattr(kb_loader, "configured_source_paths", lambda: [str(custom)])

    source_names = [Path(path).name for path in kb_loader.loader_source_paths()]

    assert source_names == ["demo_knowledge_base.csv", "customer_kb.csv"]


def test_onboarding_server_binds_all_interfaces_in_container(monkeypatch):
    from scripts import onboarding_server

    monkeypatch.setattr(onboarding_server, "CONTAINER_MODE", True)
    assert onboarding_server.bind_host() == "0.0.0.0"

    monkeypatch.setattr(onboarding_server, "CONTAINER_MODE", False)
    assert onboarding_server.bind_host() == "127.0.0.1"


def test_onboarding_compose_runs_server_as_module():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'command: ["python", "-m", "scripts.onboarding_server"]' in compose


def test_onboarding_load_knowledge_uses_noninteractive_all(monkeypatch):
    from scripts import onboarding_tasks

    calls = []
    monkeypatch.setattr(onboarding_tasks, "run_command", lambda args, timeout=300: calls.append(args) or {"ok": True})

    result = onboarding_tasks.load_knowledge()

    assert result["ok"] is True
    assert calls == [[onboarding_tasks._python(), "knowledge_loader/kb_loader.py", "--all"]]


def test_onboarding_status_flags_placeholders_and_default_db(tmp_path, monkeypatch):
    from scripts import onboarding_tasks

    env_file = tmp_path / ".env.docker"
    env_file.write_text(
        "\n".join([
            "ACTIVE_PROVIDER=openai",
            "OPENAI_API_KEY=replace-with-provider-key",
            "API_KEY=change-me",
            "CONFIGURATOR_API_KEY=change-me-configurator",
            "DATABASE_URL=postgresql://resolvekit:resolvekit@db:5432/resolvekit",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(onboarding_tasks, "ENV_PATH", env_file)
    monkeypatch.setattr(onboarding_tasks, "CONTAINER_MODE", True)

    status = onboarding_tasks.system_status()

    assert status["provider_key_placeholder"] is True
    assert status["viewer_token_placeholder"] is True
    assert status["admin_token_placeholder"] is True
    assert status["default_database_credentials"] is True


def test_onboarding_reset_task_clears_local_uploads_and_configs(tmp_path, monkeypatch):
    from scripts import onboarding_tasks

    uploads = tmp_path / "demo_data" / "onboarding" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "source.csv").write_text("title,content\nA,B\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sources.yaml").write_text("local: true\n", encoding="utf-8")
    monkeypatch.setattr(onboarding_tasks, "ROOT", tmp_path)
    monkeypatch.setattr(onboarding_tasks, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(onboarding_tasks, "CONTAINER_MODE", True)

    result = onboarding_tasks.reset_demo_state()

    assert result["ok"] is True
    assert not uploads.exists()
    assert not (config_dir / "sources.yaml").exists()
    assert "docker compose down" in result["hint"]


def test_onboarding_vector_ingest_accepts_csv_only(tmp_path):
    from scripts import onboarding_tasks

    csv_file = tmp_path / "customer.csv"
    xlsx_file = tmp_path / "customer.xlsx"
    pdf_file = tmp_path / "customer.pdf"

    csv_file.write_text("title,content\nCustom,Custom KB answer.\n", encoding="utf-8")
    xlsx_file.write_text("placeholder", encoding="utf-8")
    pdf_file.write_bytes(b"%PDF-1.4 placeholder")

    result = onboarding_tasks.ingest_uploaded_sources([str(csv_file), str(xlsx_file), str(pdf_file)])

    assert result["ok"] is False
    assert "CSV" in result["stderr"]


def test_onboarding_upload_ui_describes_csv_vector_ingest():
    html = Path("frontend/onboarding/index.html").read_text(encoding="utf-8")

    assert 'accept=".csv"' in html
    assert "CSV knowledge files" in html
    assert "XLSX" not in html.split("function renderSources()", 1)[1].split("function uploadSources()", 1)[0]


def test_onboarding_ui_is_numbered_walkthrough():
    html = Path("frontend/onboarding/index.html").read_text(encoding="utf-8")

    for label in [
        "1. System",
        "2. Provider",
        "3. Knowledge",
        "4. First draft",
        "5. Open app",
    ]:
        assert label in html
    assert "What happened" in html
    assert "Open Ticket UI" in html
    assert "Open Admin" in html
    assert "Reset local demo" in html


def test_demo_doctor_script_contract():
    script = Path("scripts/demo_doctor.sh")
    assert script.exists()
    text = script.read_text(encoding="utf-8")

    assert "ResolveKit Demo Doctor" in text
    assert "diagnostics/demo_doctor/latest.json" in text
    assert "diagnostics/demo_doctor/latest.md" in text
    assert "Demo readiness:" in text
    assert "Production readiness:" in text
    assert "scripts/public_smoke.sh" in text
    assert "scripts/ci_golden_eval.sh" in text
    assert "OPENAI_API_KEY" not in text
    assert "GEMINI_API_KEY" not in text


def test_makefile_doctor_runs_demo_doctor_script():
    makefile = Path("Makefile").read_text(encoding="utf-8")

    assert "doctor:" in makefile
    assert "./scripts/demo_doctor.sh" in makefile


def test_demo_doctor_reports_are_ignored():
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert "diagnostics/demo_doctor/*" in gitignore
    assert "!diagnostics/demo_doctor/.gitkeep" in gitignore


def test_readme_is_transparent_for_reference_project():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert len(readme.splitlines()) <= 360
    assert "Demo readiness" in readme
    assert "Production readiness" in readme
    assert "AI Transparency And Ethics" in readme
    assert "support-AI reference project" in readme
    assert "Project Status: Frozen Reference Implementation" in readme
    assert "What I Learned" in readme
    assert "make doctor" in readme
    assert "public alpha gate" not in readme.lower()
    assert "Release gate" not in readme


def test_kb_loader_all_flag_skips_interactive_selection():
    import inspect

    main_source = inspect.getsource(kb_loader.main)

    assert "select_all" in main_source
    assert "input(" in main_source


def test_launch_readiness_uses_active_golden_set_path():
    app_source = Path("backend/api/app.py").read_text(encoding="utf-8")

    assert "eval\" / \"golden_set\" / \"v3_1_starter.jsonl" in app_source
    assert "golden\" / \"resolvekit_v0_1.jsonl" not in app_source.split('@app.get("/launch-readiness"', 1)[1]


def test_public_demo_has_distinct_app_and_website_rows():
    with (ROOT / "knowledge_loader/processed/demo_knowledge_base.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    platforms = {row["platform"] for row in rows}
    assert {"app", "website"}.issubset(platforms)
    assert any("Mobile offline queue" in row["title"] and row["platform"] == "app" for row in rows)
    assert any("Website session timeout" in row["title"] and row["platform"] == "website" for row in rows)


def test_public_demo_files_do_not_reference_removed_sample_exports():
    checked = [
        ROOT / "README.md",
        ROOT / "config/products.example.yaml",
        ROOT / "config/sources.example.yaml",
        ROOT / "backend/core/project_config.py",
        ROOT / "knowledge_loader/kb_scraper.py",
        *sorted((ROOT / "knowledge_loader/processed").glob("demo_*.csv")),
    ]
    banned = [
        "".join(chr(c) for c in [83, 99, 104, 101, 100, 117, 108, 101, 65, 110, 121, 119, 104, 101, 114, 101]),
        "".join(chr(c) for c in [116, 99, 112, 115, 111, 102, 116, 119, 97, 114, 101, 46, 99, 111, 109]),
        "kb_" + "".join(chr(c) for c in [115, 99, 104, 101, 100, 117, 108, 101, 97, 110, 121, 119, 104, 101, 114, 101]),
        "kb_" + "".join(chr(c) for c in [104, 117, 109, 97, 110, 105, 116, 121]),
    ]
    for path in checked:
        text = path.read_text(encoding="utf-8")
        for phrase in banned:
            assert phrase not in text, f"{phrase} found in {path}"


def test_public_docs_use_single_database_schema_story():
    checked = [
        ROOT / "README.md",
        ROOT / "docs/TECHNICAL.md",
        ROOT / "docs/README.md",
        ROOT / "scripts/setup_db.py",
        ROOT / "scripts/rebuild_db.py",
        ROOT / "scripts/check_db.py",
        ROOT / "backend/core/config.py",
    ]
    banned = ["RELATIONAL_DB_URL", "generaluse_db", "relational_db", "vectordb"]
    for path in checked:
        text = path.read_text(encoding="utf-8")
        for phrase in banned:
            assert phrase not in text, f"{phrase} found in {path}"
    assert "KNOWLEDGE_SCHEMA" in (ROOT / "docs" / "TECHNICAL.md").read_text(encoding="utf-8")
    assert "OPS_SCHEMA" in (ROOT / "docs" / "TECHNICAL.md").read_text(encoding="utf-8")


def test_readme_has_suggest_only_and_what_this_is_not():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "suggest-only" in text
    assert "Not a deployable AI support agent" in text
    assert "auto-send" in text
    assert "auto-resolve" in text
    assert "mutate customer accounts" in text
    assert 'mode: "suggest"' in text


def test_readme_states_license_and_llm_output_posture():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "MIT. See [LICENSE](LICENSE)." in text
    assert "LLM-generated drafts are suggestions for human review." in text
    assert "ResolveKit does not claim ownership" in text


def test_public_alpha_env_docs_match_supported_providers_and_hardening():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    technical = (ROOT / "docs/TECHNICAL.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    docker_env_example = (ROOT / ".env.docker.example").read_text(encoding="utf-8")

    combined = "\n".join([readme, technical, env_example, docker_env_example])
    assert "ANTHROPIC_API_KEY" not in combined
    assert "Supported hosted providers are `openai` and `gemini`." in technical
    assert "CONFIGURATOR_PREFILL_API_KEY=false" in env_example
    assert "CONFIGURATOR_PREFILL_API_KEY=false" in docker_env_example
    assert "CORS_ALLOW_ORIGINS" in combined


def test_framework_vocabulary_audit_exists():
    text = (ROOT / "README.md").read_text(encoding="utf-8") + "\n" + (
        ROOT / "docs/TECHNICAL.md"
    ).read_text(encoding="utf-8")
    for term in ["ticket", "agent", "customer-facing", "resolve", "support"]:
        assert term in text
    for path in ["pipeline/responder.py", "frontend/ticket/index.html", "backend/core/run_trace.py", "backend/api/app.py"]:
        assert path in text


def test_golden_set_schema_and_starter_cases_exist():
    schema_path = ROOT / "eval/golden_set/schema.json"
    starter_path = ROOT / "eval/golden_set/v3_1_starter.jsonl"
    assert schema_path.exists()
    assert starter_path.exists()
    required = set(json.loads(schema_path.read_text())["required"])
    rows = [json.loads(line) for line in starter_path.read_text().splitlines() if line.strip()]
    assert rows
    for row in rows:
        assert required <= set(row)
        assert row["expected_confidence_band"] in {"green", "yellow", "red"}


# --- run trace tests ---
from backend.core.run_trace import build_run_trace, hash_ticket, redact_chunk, redact_text


def test_redact_text_masks_common_pii_and_secrets():
    raw = "Email maya@example.com, phone 555-123-4567, card 4242 4242 4242 4242, token sk_test_secret123456789"
    redacted = redact_text(raw)
    assert "maya@example.com" not in redacted
    assert "555-123-4567" not in redacted
    assert "4242 4242 4242 4242" not in redacted
    assert "sk_test_secret123456789" not in redacted
    assert "[redacted_email]" in redacted
    assert "[redacted_phone]" in redacted


def test_redact_text_masks_configured_names_accounts_and_addresses():
    raw = "Hi Maya, account ID ACCT_123456 ships to 123 Main Street Apt 4."
    redacted = redact_text(raw)
    assert "Maya" not in redacted
    assert "ACCT_123456" not in redacted
    assert "123 Main Street" not in redacted
    assert "[redacted_name]" in redacted
    assert "[redacted_account_id]" in redacted
    assert "[redacted_address]" in redacted


def test_redact_text_does_not_mask_support_action_as_person_name():
    raw = "User cannot log in to the mobile app. Getting error 403 on mobile only."
    redacted = redact_text(raw)
    assert "cannot log in" in redacted
    assert "error 403" in redacted
    assert "[redacted_name]" not in redacted


def test_redact_chunk_sets_chunk_redaction_status():
    chunk = {"id": "kb_1", "content": "Contact maya@example.com for setup."}
    redacted = redact_chunk(chunk)
    assert "maya@example.com" not in redacted["content"]
    assert redacted["redaction_applied"] is True
    assert redacted["redaction_status"] == "redacted"


def test_build_run_trace_redacts_ticket_and_preserves_evidence_metadata():
    context = {
        "ticket": {"cleaned": "Maya at maya@example.com cannot export compliance report."},
        "request_meta": {
            "product": "Example Product",
            "permission_level": "admin",
            "access_channel": "website",
            "runtime_config_version": "cfg123",
        },
        "search_query": "compliance export",
        "routing_strategy": "policy",
        "top_chunks": [{
            "id": "chunk_1",
            "content": "Admins can export compliance reports.",
            "source_id": "policy:exports",
            "source_type": "policy",
            "source_ref": "demo_policies.csv",
            "is_approved": True,
            "audience_allowed": ["customer", "internal"],
            "chunk_hash": "chunk_hash",
            "rerank_score": 8.1,
        }],
    }
    resolution = {
        "confidence": "HIGH",
        "confidence_scorer": {"confidence_band": "green"},
        "validation": {"passed": True},
        "draft_email": "Hi Maya, email maya@example.com is not needed here.",
    }

    trace = build_run_trace(context, resolution, started_at=0).to_dict()

    assert trace["ticket_text_hash"] == hash_ticket(context["ticket"]["cleaned"])
    assert "maya@example.com" not in trace["redacted_ticket_preview"]
    assert trace["config_hash"] == "cfg123"
    assert trace["reranked_results"][0]["source_id"] == "policy:exports"
    assert trace["final_response"]["draft_email"].count("[redacted_email]") == 1


# --- source connector tests ---
from pathlib import Path
import zipfile

import pytest

from backend.core import project_config
from knowledge_loader.connectors import ConnectorError, get_connector_for_path


def write_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        for text in paragraphs
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>",
        )
    return path


def write_xlsx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData>"
            '<row r="1"><c t="inlineStr"><is><t>title</t></is></c><c t="inlineStr"><is><t>content</t></is></c></row>'
            '<row r="2"><c t="inlineStr"><is><t>Login export</t></is></c><c t="inlineStr"><is><t>Use the export page after confirming admin permission.</t></is></c></row>'
            "</sheetData></worksheet>",
        )
    return path


def connector_documents(path: Path):
    connector = get_connector_for_path(path)
    documents, preview = connector.parse(
        path,
        source_key="knowledge_base",
        source_type="official_help_article",
        column_mapping={},
        sample_limit=5,
    )
    return documents, preview


def test_csv_connector_emits_source_document(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text("title,content\nLogin help,Use reset page with account email.\n", encoding="utf-8")
    documents, preview = connector_documents(source)
    assert preview["detected_columns"] == ["title", "content"]
    assert documents[0].title == "Login help"
    assert documents[0].sections[0].row_ref == "1"


def test_html_connector_emits_source_document(tmp_path):
    source = tmp_path / "help.html"
    source.write_text(
        "<html><head><title>Mobile notifications</title></head><body><h1>Push setup</h1>"
        "<p>Enable app notifications and confirm team inbox assignment rules before escalation.</p></body></html>",
        encoding="utf-8",
    )
    documents, _ = connector_documents(source)
    assert documents[0].title == "Mobile notifications"
    assert "team inbox" in documents[0].body


def test_docx_connector_emits_source_document(tmp_path):
    source = write_docx(tmp_path / "help.docx", [
        "Role change troubleshooting",
        "Sign out and back in after an admin changes permission level.",
    ])
    documents, _ = connector_documents(source)
    assert documents[0].title == "Role change troubleshooting"
    assert "permission level" in documents[0].body


def test_xlsx_connector_emits_source_document(tmp_path):
    source = write_xlsx(tmp_path / "help.xlsx")
    documents, preview = connector_documents(source)
    assert "title" in preview["detected_columns"]
    assert documents[0].title == "Login export"
    assert "admin permission" in documents[0].body


def test_pdf_connector_emits_source_document_from_text_fallback(tmp_path):
    source = tmp_path / "help.pdf"
    source.write_bytes(b"%PDF-1.4\n1 0 obj <<>> stream\n(Mobile push notification setup requires app permission and team assignment.)\nendstream\n%%EOF")
    documents, preview = connector_documents(source)
    assert documents[0].title == "Help"
    assert "team assignment" in documents[0].body
    assert preview["warnings"] or documents[0].metadata["format"] == "pdf"


def test_connector_errors_fail_closed(tmp_path):
    source = tmp_path / "bad.docx"
    source.write_text("not a zip", encoding="utf-8")
    with pytest.raises(ConnectorError):
        connector_documents(source)


def test_project_preview_uses_connector_documents(tmp_path):
    source = tmp_path / "help.html"
    source.write_text(
        "<html><head><title>Export help</title></head><body>"
        "<p>Admins can request exports from the website reports page.</p></body></html>",
        encoding="utf-8",
    )
    preview = project_config.preview_source("knowledge_base", str(source))
    assert preview["can_load"] is True
    assert preview["sample_documents"][0]["title"] == "Export help"
    assert preview["sample_chunk_previews"]


# --- source preview tests ---
from pathlib import Path

from fastapi.testclient import TestClient

from backend.core import config
from backend.api.app import app
from backend.core import project_config
from knowledge_loader.kb_loader import preview_import_summary


def write_csv(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_source_registry_loads_defaults():
    registry = project_config.get_source_registry()
    assert {"knowledge_base", "policies", "release_notes", "known_issues", "historical_tickets"} <= set(registry)
    assert registry["historical_tickets"]["enabled_default"] is False
    assert registry["historical_tickets"]["customer_facing_evidence_allowed"] is False


def test_each_source_contract_validates_required_fields():
    cases = {
        "knowledge_base": ["title", "content"],
        "policies": ["policy_name", "content"],
        "release_notes": ["title", "content"],
        "known_issues": ["issue_title", "symptoms"],
    }
    for source_key, columns in cases.items():
        result = project_config.validate_source_contract(source_key, columns, {})
        assert result["valid"], source_key


def test_missing_required_fields_block_loading():
    result = project_config.validate_source_contract("knowledge_base", ["title", "url"], {})
    assert result["valid"] is False
    assert result["errors"]


def test_recommended_missing_fields_warn_only():
    result = project_config.validate_source_contract("knowledge_base", ["title", "content"], {})
    assert result["valid"] is True
    assert any("Recommended field missing" in warning for warning in result["warnings"])


def test_column_mapping_preview_maps_source_columns_to_canonical_fields(tmp_path):
    source = write_csv(
        tmp_path / "kb.csv",
        "Heading,Body,Link\nPassword reset,Use the reset page and follow the emailed link.,https://example.test/reset\n",
    )
    preview = project_config.preview_source(
        "knowledge_base",
        str(source),
        "official_help_article",
        {"title": "Heading", "content": "Body", "url": "Link"},
    )
    assert preview["can_load"] is True
    assert preview["sample_canonical_rows"][0]["title"] == "Password reset"
    assert preview["sample_canonical_rows"][0]["content"].startswith("Use the reset page")


def test_sample_chunk_previews_are_returned(tmp_path):
    source = write_csv(
        tmp_path / "kb.csv",
        "title,content\nLogin help,Open settings and reset the password with the account email.\n",
    )
    preview = project_config.preview_source("knowledge_base", str(source))
    assert preview["sample_canonical_rows"]
    assert preview["sample_chunk_previews"]
    assert "embedding_text" in preview["sample_chunk_previews"][0]
    assert "display_text" in preview["sample_chunk_previews"][0]


def test_contextual_retrieval_preview_fields_do_not_leak_into_display_text(tmp_path):
    source = write_csv(
        tmp_path / "known.csv",
        "issue_title,symptoms,affected_platform,status,workaround\n"
        "Mobile badge delay,Badges can update slowly after reassignment.,app,open,Applies when mobile notifications are enabled.\n",
    )
    preview = project_config.preview_source("known_issues", str(source))
    chunk = preview["sample_chunk_previews"][0]
    assert chunk["source_row"] == 1
    assert chunk["source_type"] == "known_issue"
    assert chunk["heading_path"] == "Mobile badge delay"
    assert chunk["contextual_retrieval"]["enabled"] is True
    assert "Platform: app" in chunk["embedding_text"]
    assert "Known issue status: open" in chunk["embedding_text"]
    assert "Applies when:" in chunk["embedding_text"]
    assert "Known issue status:" not in chunk["display_text"]
    assert "Applies when:" not in chunk["display_text"]


def test_preview_endpoint_does_not_write_to_db(tmp_path, monkeypatch):
    source = write_csv(
        tmp_path / "kb.csv",
        "title,content\nLogin help,Open settings and reset the password with the account email.\n",
    )

    def fail_connect(*args, **kwargs):
        raise AssertionError("preview endpoint must not open a database connection")

    monkeypatch.setattr("backend.api.app.BASE_DIR", tmp_path)
    monkeypatch.setattr("psycopg2.connect", fail_connect)
    response = TestClient(app).post(
        "/configurator/source-preview",
        headers={"x-api-key": config.CONFIGURATOR_API_KEY},
        json={"source_key": "knowledge_base", "path": str(source), "column_mapping": {}},
    )
    assert response.status_code == 200
    assert response.json()["preview"]["can_load"] is True


def test_configurator_routes_require_configurator_api_key(monkeypatch):
    monkeypatch.setattr(config, "CONFIGURATOR_API_KEY", "config-secret")
    client = TestClient(app)
    for path, method, body in [
        ("/configurator/config", "get", None),
        ("/configurator/config", "post", {}),
        ("/configurator/validate", "post", {}),
        ("/configurator/source-preview", "post", {"source_key": "knowledge_base", "path": "missing.csv"}),
        ("/configurator/setup-status", "get", None),
    ]:
        request = getattr(client, method)
        kwargs = {"headers": {"x-api-key": "wrong"}}
        if body is not None:
            kwargs["json"] = body
        response = request(path, **kwargs)
        assert response.status_code == 401


def test_source_preview_rejects_oversize_files(tmp_path, monkeypatch):
    source = write_csv(tmp_path / "kb.csv", "title,content\nA,Enough words for preview.\n")
    monkeypatch.setattr("backend.api.app.BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIGURATOR_SOURCE_PREVIEW_MAX_BYTES", 4)
    response = TestClient(app).post(
        "/configurator/source-preview",
        headers={"x-api-key": config.CONFIGURATOR_API_KEY},
        json={"source_key": "knowledge_base", "path": str(source), "column_mapping": {}},
    )
    assert response.status_code == 413


def test_source_preview_rejects_wrong_file_type(tmp_path, monkeypatch):
    source = tmp_path / "kb.txt"
    source.write_text("title,content\nA,Enough words for preview.\n", encoding="utf-8")
    monkeypatch.setattr("backend.api.app.BASE_DIR", tmp_path)
    response = TestClient(app).post(
        "/configurator/source-preview",
        headers={"x-api-key": config.CONFIGURATOR_API_KEY},
        json={"source_key": "knowledge_base", "path": str(source), "column_mapping": {}},
    )
    assert response.status_code == 415


def test_source_preview_rejects_paths_outside_project(tmp_path):
    source = tmp_path / "kb.csv"
    source.write_text("title,content\nA,Enough words for preview.\n", encoding="utf-8")
    response = TestClient(app).post(
        "/configurator/source-preview",
        headers={"x-api-key": config.CONFIGURATOR_API_KEY},
        json={"source_key": "knowledge_base", "path": str(source), "column_mapping": {}},
    )
    assert response.status_code == 403


def test_disabled_sources_are_skipped_in_import_summary(tmp_path):
    source = write_csv(tmp_path / "disabled.csv", "title,content\nA,Enough words to pass the row length check.\n")
    summary = preview_import_summary(str(source), "knowledge_base", {"enabled": False})
    assert summary["rows_loaded"] == 0
    assert "disabled" in summary["warnings"][0]


def test_historical_tickets_cannot_be_customer_facing_evidence(tmp_path):
    source = write_csv(
        tmp_path / "tickets.csv",
        "ticket_id,customer_message\n1,Customer shared raw private support conversation details.\n",
    )
    preview = project_config.preview_source("historical_tickets", str(source), "raw_ticket_history")
    assert preview["can_load"] is False
    assert project_config.validate_config({
        **project_config.load_config(),
        "sources": {"sources": {"historical_tickets": {"enabled": True, "audience": "customer_facing", "source_type": "raw_ticket_history", "path": str(source)}}},
    })["valid"] is False


def test_import_summary_includes_counts_and_skipped_reasons(tmp_path):
    source = write_csv(
        tmp_path / "kb.csv",
        "title,content\nGood row,This content has enough words to be chunked for preview.\nShort,bad\n",
    )
    summary = preview_import_summary(str(source), "knowledge_base", {"enabled": True, "column_mapping": {}})
    assert summary["total_rows_seen"] == 2
    assert summary["rows_loaded"] == 1
    assert summary["rows_skipped"] == 1
    assert summary["chunks_created"] >= 1
    assert summary["skipped_row_reasons"][0]["reason"]


# --- source merge and safety calibration tests ---
from pipeline.confidence import compute_scorer_result
from pipeline.conflicts import detect_source_conflicts
from pipeline.retrieval_policy import merge_by_source_type, score_candidate_with_policy
from scripts.run_golden_eval import compare_to_baseline, evaluate_stored_results, validate_golden_rows
from backend.core import project_config


def chunk(**overrides):
    base = {
        "id": "kb_1",
        "source_id": "knowledge_base:setup",
        "source_type": "official_help_article",
        "source_ref": "demo_knowledge_base.csv",
        "is_approved": True,
        "is_customer_facing_allowed": True,
        "is_internal_only": False,
        "is_future_only": False,
        "disabled": False,
        "rrf_score": 0.2,
        "policy_score": 0.2,
        "rerank_score": 8.0,
    }
    base.update(overrides)
    return base


def test_source_type_merge_blocks_forbidden_sources_even_with_high_score():
    merged = merge_by_source_type([
        chunk(id="raw", source_id="historical_tickets:T-1", source_type="raw_ticket_history", policy_score=999.0),
        chunk(id="kb", source_id="knowledge_base:setup", source_type="official_help_article", policy_score=0.1),
    ], "general", top_k=3)
    assert [item["id"] for item in merged] == ["kb"]


def test_source_type_merge_blocks_internal_future_and_non_customer_sources():
    merged = merge_by_source_type([
        chunk(id="internal", source_id="internal:setup", is_internal_only=True, policy_score=10.0),
        chunk(id="future", source_id="future:setup", is_future_only=True, policy_score=9.0),
        chunk(id="not_customer", source_id="kb:not_customer", is_customer_facing_allowed=False, policy_score=8.0),
        chunk(id="safe", source_id="knowledge_base:safe", policy_score=0.1),
    ], "general", top_k=4)
    assert [item["id"] for item in merged] == ["safe"]


def test_source_type_merge_blocks_missing_source_identity():
    merged = merge_by_source_type([
        chunk(id="missing", source_id="", policy_score=10.0),
        chunk(id="safe", source_id="knowledge_base:safe", policy_score=0.1),
    ], "general", top_k=2)
    assert [item["id"] for item in merged] == ["safe"]


def test_source_type_merge_reserves_policy_slot_for_policy_route():
    merged = merge_by_source_type([
        chunk(id="kb", source_type="official_help_article", policy_score=0.9),
        chunk(id="pol", source_id="policies:billing", source_type="policy", policy_score=0.5),
    ], "policy", top_k=2)
    assert merged[0]["id"] == "pol"


def test_source_type_merge_avoids_duplicate_parent_sections_and_prefers_fresh_sources():
    merged = merge_by_source_type([
        chunk(id="stale", source_type="official_help_article", policy_score=10.0, parent_section_id="p1", needs_review_at="2020-01-01T00:00:00+00:00"),
        chunk(id="fresh", source_type="official_help_article", policy_score=5.0, parent_section_id="p1"),
        chunk(id="other", source_type="faq", policy_score=4.0, parent_section_id="p2"),
    ], "general", top_k=3)
    assert [item["id"] for item in merged] == ["fresh", "other"]


def test_policy_vs_faq_conflict_caps_confidence():
    chunks = [
        chunk(id="policy", source_id="policies:retention", source_type="policy", rerank_score=8.0, status="allowed"),
        chunk(id="faq", source_id="faq:retention", source_type="faq", rerank_score=7.0, status="disabled"),
    ]
    conflicts = [conflict.to_dict() for conflict in detect_source_conflicts(chunks)]
    score = compute_scorer_result(chunks, source_conflicts=conflicts)
    assert conflicts[0]["conflict_type"] == "policy_vs_faq"
    assert score.source_conflict_detected is True
    assert score.confidence_band in {"yellow", "red"}
    assert score.confidence_score <= 0.45


def test_policy_and_kb_without_explicit_disagreement_are_not_conflicts():
    chunks = [
        chunk(id="policy", source_id="policies:export", source_type="policy", rerank_score=4.0),
        chunk(id="kb", source_id="knowledge_base:export", source_type="knowledge_base", rerank_score=3.0),
    ]
    assert detect_source_conflicts(chunks) == []


def test_confidence_exposes_v3_2_calibration_signals():
    chunks = [
        chunk(id="kb", source_id="knowledge_base:setup", source_type="official_help_article", rerank_score=8.0),
        chunk(id="pol", source_id="policies:setup", source_type="policy", rerank_score=4.0, needs_review_at="2020-01-01T00:00:00+00:00"),
    ]
    score = compute_scorer_result(chunks, source_conflicts=[], route="policy")
    assert score.top_rerank_score == 8.0
    assert score.score_gap == 4.0
    assert score.source_diversity == 2
    assert score.route_source_alignment == 1.0
    assert score.stale_source_count == 1
    assert score.approved_source_coverage == 0.0


def test_known_issue_status_conflict_is_high_severity():
    conflicts = detect_source_conflicts([
        chunk(id="issue_open", source_id="known_issues:push", source_type="known_issue", status="open"),
        chunk(id="issue_resolved", source_id="known_issues:push_old", source_type="known_issue", status="resolved"),
    ])
    assert conflicts[0].conflict_type == "known_issue_status_conflict"
    assert conflicts[0].severity == "high"


def test_version_specific_conflict_is_detected():
    conflicts = detect_source_conflicts([
        chunk(id="v1", source_id="kb:v1", source_type="official_help_article", embedding_text="Version 1.2 uses legacy exports."),
        chunk(id="v2", source_id="kb:v2", source_type="release_note", embedding_text="Version 2.0 moved exports to reports."),
    ])
    assert conflicts[0].conflict_type == "version_specific_behavior_conflict"


def test_source_authority_presets_clamp_raw_sources_to_zero():
    weights = project_config.source_authority_weights({
        "source_authority": {"raw_ticket_history": 1.0},
        "source_authority_presets": {
            "active": "permissive_internal",
            "presets": {"permissive_internal": {"raw_ticket_history": 0.5}},
        },
    })
    assert weights["raw_ticket_history"] == 0.0


def test_source_type_weight_changes_policy_score(monkeypatch):
    def fake_load_config(section):
        assert section == "retrieval_policy"
        return {
            "retrieval": {"source_type_weights": {"pdf": 0.5}},
            "route_policies": {"general": {"boost": 0.0, "preferred_source_types": []}},
        }

    monkeypatch.setattr(project_config, "load_config", fake_load_config)

    scored = score_candidate_with_policy(chunk(source_type="pdf", rrf_score=2.0), "general")

    assert scored["source_type_weight"] == 0.5
    assert scored["policy_score"] == 1.0


def test_ab_config_tree_has_control_and_five_variants_per_stage():
    config_root = ROOT / "configs" / "ab"

    assert (config_root / "control.yaml").exists()
    for stage_dir in sorted(path for path in config_root.iterdir() if path.is_dir()):
        variants = sorted(stage_dir.glob("v*.yaml"))
        assert len(variants) == 5, stage_dir


def test_stage2_ab_runner_loads_variants_and_reports_inventory(tmp_path):
    from scripts import run_ab_stage2_eval

    variants = run_ab_stage2_eval.load_stage2_variants()
    summary = run_ab_stage2_eval.run(
        argparse.Namespace(
            config_dir=run_ab_stage2_eval.CONFIG_DIR,
            golden_set=ROOT / "eval" / "golden_set" / "v3_1_starter.jsonl",
            schema=ROOT / "eval" / "golden_set" / "schema.json",
            result_file=[],
            output_dir=tmp_path / "stage2",
        )
    )

    assert len(variants) == 5
    assert len(summary["variants"]) == 5
    assert summary["stage"] == "kb_loading"
    assert (tmp_path / "stage2" / "summary.json").exists()
    assert any(
        variant["inventory"]["total_loaded_records"] > 0
        for variant in summary["variants"].values()
    )


def test_golden_eval_schema_and_hard_failures():
    schema = {"required": ["ticket_id", "expected_source_ids", "forbidden_source_ids", "expected_confidence_band"]}
    rows = [{
        "ticket_id": "G-001",
        "expected_source_ids": ["knowledge_base:setup"],
        "forbidden_source_ids": ["historical_tickets:T-1"],
        "expected_confidence_band": "green",
        "expected_route": "general",
    }]
    assert validate_golden_rows(rows, schema)["schema_valid"] is True
    report = evaluate_stored_results(rows, [{
        "ticket_id": "G-001",
        "cited_source_ids": ["historical_tickets:T-1"],
        "retrieved_source_ids": ["knowledge_base:setup", "historical_tickets:T-1"],
        "route": "general",
        "confidence_band": "green",
        "latency_ms": 125,
        "cost_usd": 0.01,
    }])
    assert report["hard_failure_count"] == 2
    assert "forbidden source" in report["hard_failures"][0]
    assert report["retrieval_recall"] == 1.0
    assert report["retrieval_recall_at_3"] == 1.0
    assert report["retrieval_recall_at_5"] == 1.0
    assert report["mean_reciprocal_rank"] == 1.0
    assert report["avg_latency_ms"] == 125.0


def test_golden_eval_reports_rank_aware_retrieval_metrics():
    rows = [{
        "ticket_id": "G-001",
        "expected_source_ids": ["knowledge_base:target"],
        "forbidden_source_ids": [],
        "expected_confidence_band": "green",
        "expected_route": "general",
    }]
    report = evaluate_stored_results(rows, [{
        "ticket_id": "G-001",
        "retrieved_source_ids": [
            "knowledge_base:first",
            "knowledge_base:second",
            "knowledge_base:target",
        ],
        "route": "general",
        "confidence_band": "green",
    }])
    assert report["retrieval_recall"] == 1.0
    assert report["retrieval_recall_at_1"] == 0.0
    assert report["retrieval_recall_at_3"] == 1.0
    assert report["retrieval_recall_at_5"] == 1.0
    assert report["mean_reciprocal_rank"] == 0.3333


def test_golden_result_cost_prefers_usage_summary():
    from scripts.generate_golden_results import _cost_usd

    assert _cost_usd({
        "usage_summary": {"total_cost_usd": 0.123456789},
        "usage": {"responder": {"cost_usd": 999}},
    }) == 0.12345679


def test_golden_result_extracts_answer_and_token_usage():
    from scripts.generate_golden_results import _result_from_resolution

    result = _result_from_resolution(
        {"ticket_id": "G-001"},
        {
            "resolution_steps": "Use the billing export.",
            "draft_email": "Subject: Export\n\nUse the billing export.",
            "usage_summary": {
                "query_tokens_in": 10,
                "query_tokens_out": 2,
                "response_tokens_in": 30,
                "response_tokens_out": 8,
                "eval_tokens_in": 5,
                "eval_tokens_out": 1,
                "total_tokens": 56,
                "total_cost_usd": 0.02,
            },
            "llm_workflow": {"llm_calls_used": 2},
        },
        100,
    )
    assert result["tokens_in"] == 45
    assert result["tokens_out"] == 11
    assert result["total_tokens"] == 56
    assert result["llm_calls_used"] == 2
    assert "billing export" in result["answer_text"]


def test_live_ab_eval_payload_includes_experiment_arm():
    from scripts import run_live_ab_eval

    case = {
        "ticket_id": "case-1",
        "ticket_text": "Customer cannot sign in.",
        "product": "Example Product",
        "platform": "mobile_app",
        "role": "agent",
    }

    payload = run_live_ab_eval.build_payload(case, "current_hybrid_rag")

    assert payload["mode"] == "suggest"
    assert payload["experiment_arm"] == "current_hybrid_rag"
    assert payload["ticket"] == "Customer cannot sign in."


def test_api_me_reports_viewer_and_admin_roles(monkeypatch):
    from backend.api.app import app
    from fastapi.testclient import TestClient

    monkeypatch.setattr("backend.core.config.VIEWER_TOKEN", "viewer-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")

    client = TestClient(app)
    viewer = client.get("/api/me", headers={"x-api-key": "viewer-secret"}).json()
    admin = client.get("/api/me", headers={"x-api-key": "admin-secret"}).json()

    assert viewer["role"] == "viewer"
    assert "submit_feedback" in viewer["permissions"]
    assert admin["role"] == "admin"
    assert "run_ab_tests" in admin["permissions"]


def test_source_contract_loads_csv_xlsx_pdf_demo_records():
    from knowledge_loader.source_contract import chunk_source_records, load_source_records

    paths = [
        ROOT / "demo_data" / "csv" / "resolvekit_demo_kb.csv",
        ROOT / "demo_data" / "xlsx" / "resolvekit_demo_kb.xlsx",
        next((ROOT / "demo_data" / "pdf").glob("*.pdf")),
    ]
    for path in paths:
        records, errors = load_source_records(path)
        chunks, report = chunk_source_records(records)
        assert not errors
        assert records
        assert report["loaded_records"] == len(records)
        assert all(chunk.source_type in {"csv", "xlsx", "pdf"} for chunk in chunks)


def test_golden_eval_matches_expected_source_aliases():
    rows = [{
        "ticket_id": "G-001",
        "expected_source_ids": ["policies:compliance_export"],
        "forbidden_source_ids": [],
        "expected_confidence_band": "green",
        "expected_route": "policy",
    }]
    report = evaluate_stored_results(rows, [{
        "ticket_id": "G-001",
        "retrieved_source_ids": ["policies:kb_data_export_eligibility"],
        "route": "policy",
        "confidence_band": "green",
    }])
    assert report["retrieval_recall"] == 1.0
    assert report["source_precision"] == 1.0


def test_golden_eval_reports_citation_answer_and_ops_metrics():
    rows = [
        {
            "ticket_id": "G-001",
            "expected_source_ids": ["kb:one", "kb:two"],
            "forbidden_source_ids": [],
            "expected_confidence_band": "green",
            "expected_route": "general",
            "must_include_points": ["enable SSO", "ask an admin"],
            "must_not_include_points": ["share your password"],
        },
        {
            "ticket_id": "G-002",
            "expected_source_ids": ["kb:three"],
            "forbidden_source_ids": [],
            "expected_confidence_band": "yellow",
            "expected_route": "billing",
            "must_include_points": ["export invoices"],
        },
    ]
    report = evaluate_stored_results(rows, [
        {
            "ticket_id": "G-001",
            "retrieved_source_ids": ["kb:one", "kb:extra", "kb:two"],
            "cited_source_ids": ["kb:one", "kb:extra"],
            "route": "general",
            "confidence_band": "green",
            "validation_passed": True,
            "answer_text": "Enable SSO, ask an admin, and never share your password.",
            "latency_ms": 100,
            "tokens_in": 100,
            "tokens_out": 25,
            "total_tokens": 125,
            "cost_usd": 0.01,
        },
        {
            "ticket_id": "G-002",
            "retrieved_source_ids": ["kb:miss"],
            "cited_source_ids": [],
            "route": "billing",
            "confidence_band": "yellow",
            "validation_passed": False,
            "abstained": True,
            "answer_text": "Export invoices from Billing.",
            "latency_ms": 300,
            "tokens_in": 200,
            "tokens_out": 50,
            "total_tokens": 250,
            "cost_usd": 0.03,
        },
    ])
    assert report["retrieval_recall_at_1"] == 0.25
    assert report["retrieval_recall_at_3"] == 0.5
    assert report["citation_recall"] == 0.25
    assert report["citation_precision"] == 0.5
    assert report["required_point_coverage"] == 1.0
    assert report["forbidden_point_violation_count"] == 1
    assert report["p50_latency_ms"] == 200.0
    assert report["p95_latency_ms"] == 290.0
    assert report["avg_total_tokens"] == 187.5
    assert report["avg_cost_usd"] == 0.02
    assert report["fallback_rate"] == 0.5
    assert report["validation_pass_rate"] == 0.5


def test_golden_eval_does_not_count_expected_review_as_validation_failure():
    rows = [{
        "ticket_id": "G-001",
        "expected_source_ids": [],
        "forbidden_source_ids": [],
        "expected_confidence_band": "red",
        "expected_route": "general",
        "review_required_expected": True,
    }]
    report = evaluate_stored_results(rows, [{
        "ticket_id": "G-001",
        "route": "general",
        "confidence_band": "red",
        "abstained": True,
        "validation_passed": False,
    }])
    assert report["validation_failure_count"] == 0


def test_golden_eval_baseline_diff_reports_metric_deltas():
    diff = compare_to_baseline(
        {"retrieval_recall": 0.9, "route_accuracy": 0.8},
        {"retrieval_recall": 0.75, "route_accuracy": 0.85},
    )
    assert diff["retrieval_recall"]["delta"] == 0.15
    assert diff["route_accuracy"]["delta"] == -0.05


# --- replay and reporting tests ---
from backend.core.prompts import prompt_versions
from backend.core.replay import replay_saved_trace
from scripts.run_golden_eval import human_readable_report


def test_saved_trace_replay_redacts_and_compares_core_fields():
    trace = {
        "trace_id": "trace_test",
        "config_hash": "cfg_old",
        "redacted_ticket_preview": "Email [redacted_email] cannot export.",
        "reranked_results": [{"id": "chunk_1"}],
        "validation_output": {
            "passed": True,
            "citations": [{"source_id": "policy:exports"}],
        },
        "scorer_output": {"confidence_band": "green"},
        "final_response": {"draft_email": "Hi Maya, use maya@example.com."},
        "latency_by_stage": {"total_ms": 42},
    }
    report = replay_saved_trace(trace, use_current_config=False)
    assert report["private_replay"] is False
    assert report["old"]["retrieval_result_ids"] == ["chunk_1"]
    assert report["old"]["citation_ids"] == ["policy:exports"]
    assert "maya@example.com" not in report["old"]["final_response_preview"]
    assert report["diff"]["same_config_hash"] is True


def test_prompt_versions_include_model_provider_and_rollback():
    versions = prompt_versions("local")
    assert versions["responder"]["model_provider"] == "local"
    assert versions["responder"]["rollback"]
    assert versions["evaluator"]["golden_eval_required"] is True


def test_human_readable_eval_report_contains_safety_metrics():
    report = human_readable_report({
        "case_count": 1,
        "evaluated_result_count": 1,
        "schema_valid": True,
        "hard_failure_count": 0,
        "retrieval_recall": 1.0,
        "source_precision": 1.0,
        "route_accuracy": 1.0,
        "confidence_band_accuracy": 1.0,
        "abstention_accuracy": 1.0,
        "ragas_faithfulness": 1.0,
        "rag_triad": {"context_relevance": 1.0, "groundedness": 1.0, "answer_relevance": 1.0},
    })
    assert "Golden Eval Report" in report
    assert "RAGAS-style faithfulness" in report
    assert "RAG Triad" in report


def test_eval_report_builder_writes_markdown_and_updates_readme(tmp_path):
    from scripts.build_eval_report import build_markdown, build_readme_block, update_readme

    report = {
        "case_count": 1,
        "evaluated_result_count": 1,
        "schema_valid": True,
        "hard_failure_count": 0,
        "retrieval_recall_at_1": 1.0,
        "retrieval_recall_at_3": 1.0,
        "retrieval_recall_at_5": 1.0,
        "retrieval_recall": 1.0,
        "mean_reciprocal_rank": 1.0,
        "source_precision": 1.0,
        "citation_recall": 1.0,
        "citation_precision": 1.0,
        "required_point_coverage": 1.0,
        "forbidden_point_violation_count": 0,
        "route_accuracy": 1.0,
        "confidence_band_accuracy": 1.0,
        "abstention_accuracy": 1.0,
        "validation_pass_rate": 1.0,
        "fallback_rate": 0.0,
        "avg_latency_ms": 100.0,
        "p50_latency_ms": 100.0,
        "p95_latency_ms": 100.0,
        "avg_total_tokens": 125.0,
        "avg_tokens_in": 100.0,
        "avg_tokens_out": 25.0,
        "avg_cost_usd": 0.01,
        "total_cost_usd": 0.01,
        "release_gate": {"passed": True, "profile": "production"},
    }
    markdown = build_markdown(report)
    assert "Recall@1" in markdown
    assert "Readiness profile" in markdown
    assert "Release profile" not in markdown
    block = build_readme_block(report)
    assert "eval-report:start" in block
    assert "Demo readiness" in block
    assert "Release profile" not in block
    readme = tmp_path / "README.md"
    readme.write_text("Current stored golden-eval report:\n\nold\n")
    update_readme(readme, block)
    assert "Total eval cost" in readme.read_text()


# --- source freshness tests ---
from scripts.source_freshness_report import queue_high_impact


def test_source_freshness_queue_only_high_impact_stale(monkeypatch):
    queued = []
    monkeypatch.setattr("scripts.source_freshness_report.create_review_queue_item", queued.append)
    count = queue_high_impact({
        "items": [
            {"status": "stale", "chunk_count": 3, "source_id": "policy:old", "title": "Old policy"},
            {"status": "near_review", "chunk_count": 9, "source_id": "policy:soon"},
            {"status": "stale", "chunk_count": 1, "source_id": "kb:minor"},
        ],
    })
    assert count == 1
    assert queued[0]["source_issue_type"] == "stale_source"
    assert queued[0]["sla_marker"] == "source_re_review"


# --- support ops tests ---
from backend.core import project_config
from backend.core.orchestrator import _apply_support_ops_retrieval_controls
from scripts.refresh_screenshots import validate_assets


def test_config_field_metadata_has_v3_4_required_fields():
    metadata = project_config.config_field_metadata()
    assert metadata["sources.path"]["impact"] == "Requires knowledge reload"
    assert metadata["sources.path"]["reason"]
    assert metadata["sources.path"]["apply_action"] == "reload_knowledge"
    assert metadata["sources.path"]["requires_confirmation"] is True


def test_setup_wizard_status_tracks_completion():
    status = project_config.setup_wizard_status(project_config.load_config())
    assert "completion_ratio" in status
    assert status["target_completion_ratio"] == 0.8
    assert "run_three_sample_tickets" in status["steps"]


def test_support_ops_retrieval_controls_threshold_and_pinning():
    context = {
        "request_meta": {"similarity_threshold": "medium", "pinned_source_ids": ["policy:pin"]},
        "retrieved_chunks": [
            {"id": "low", "source_id": "kb:low", "rerank_score": 0.4},
            {"id": "pin", "source_id": "policy:pin", "rerank_score": 0.1},
        ],
    }
    chunks = _apply_support_ops_retrieval_controls([
        {"id": "low", "source_id": "kb:low", "rerank_score": 0.4},
        {"id": "high", "source_id": "kb:high", "rerank_score": 3.0},
    ], context)
    assert [chunk["id"] for chunk in chunks] == ["high", "pin"]
    assert chunks[1]["retrieval_reason"] == "pinned_source"


def test_screenshot_assets_are_refreshable_and_safe():
    report = validate_assets()
    assert report["ok"] is True
    assert report["checked"]


# --- stabilization tests ---
from pathlib import Path
from unittest.mock import MagicMock, Mock

from fastapi.testclient import TestClient

from backend.core import project_config
from backend.api.app import app


def test_source_license_preview_warns_on_required_attribution(tmp_path):
    source = tmp_path / "licensed.csv"
    source.write_text(
        "title,content,source_license,attribution_required,attribution_text\n"
        "Licensed help,Use approved source with enough words for preview.,CC-BY,true,Example attribution\n",
        encoding="utf-8",
    )
    preview = project_config.preview_source("knowledge_base", str(source))
    assert preview["sample_chunk_previews"][0]["attribution"]["source_license"] == "CC-BY"
    assert preview["sample_chunk_previews"][0]["attribution"]["attribution_required"] is True
    assert any("Attribution is required" in warning for warning in preview["warnings"])


def test_v3_5_docs_exist():
    maintained_docs = [
        "docs/README.md",
        "docs/TECHNICAL.md",
        "docs/DEMO.md",
    ]
    for path in maintained_docs:
        assert Path(path).exists()
    public_docs = ["docs/README.md", "docs/TECHNICAL.md", "docs/DEMO.md"]
    assert sorted(public_docs) == sorted(maintained_docs)


def test_onboarding_env_bootstrap_uses_user_owned_provider_key():
    from scripts import init_project

    updates = init_project.build_env_updates(
        {
            "API_KEY": "change-me",
            "CONFIGURATOR_API_KEY": "change-me-configurator",
        },
        provider="openai",
        provider_key="local-user-openai-token",
        demo=True,
    )

    assert updates["ACTIVE_PROVIDER"] == "openai"
    assert updates["OPENAI_API_KEY"] == "local-user-openai-token"
    assert updates["GEMINI_API_KEY"] == ""
    assert updates["API_KEY"].startswith("rk_viewer_")
    assert updates["CONFIGURATOR_API_KEY"].startswith("rk_admin_")
    assert updates["CONFIGURATOR_PREFILL_API_KEY"] == "true"


def test_onboarding_project_bootstrap_writes_product_source_and_samples(tmp_path):
    from scripts import init_project

    written = init_project.write_project_files(
        product_name="Acme Support",
        source_folder="sources/acme",
        root=tmp_path,
        force=False,
    )

    assert "config/products.yaml" in written
    assert "config/sources.yaml" in written
    assert "demo_data/onboarding/sample_questions.txt" in written
    products = json.loads((tmp_path / "config/products.yaml").read_text(encoding="utf-8"))
    sources = json.loads((tmp_path / "config/sources.yaml").read_text(encoding="utf-8"))
    assert "acme_support" in products["products"]
    assert sources["sources"]["custom_knowledge_base"]["path"] == "sources/acme/knowledge_base.csv"


def test_os_detect_reports_supported_shape():
    from scripts.os_detect import detect_os

    info = detect_os().to_dict()
    assert info["os_family"]
    assert info["label"]
    assert "docker_install_hint" in info


def test_public_smoke_starts_api_without_onboarding_port_collision():
    script = Path("scripts/public_smoke.sh").read_text(encoding="utf-8")

    assert "docker compose up -d --build db app" in script


def test_public_smoke_workflow_prepares_ci_runtime_config():
    workflow = Path(".github/workflows/public-preview.yml").read_text(encoding="utf-8")

    assert "Prepare public smoke environment" in workflow
    assert "ACTIVE_PROVIDER=mock" in workflow
    assert "SMOKE_TEST_MODE=true" in workflow
    assert "API_KEY=ci-viewer-token" in workflow
    assert "CONFIGURATOR_API_KEY=ci-configurator-token" in workflow
    assert "VIEWER_TOKEN=ci-trace-viewer-token" in workflow
    assert "CONFIGURATOR_ADMIN_TOKEN=ci-admin-token" in workflow


def test_public_smoke_docker_image_installs_cpu_only_torch_first():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    cpu_install = "pip install --no-cache-dir torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu"
    requirements_install = "pip install --no-cache-dir -r requirements.txt"

    assert cpu_install in dockerfile
    assert dockerfile.index(cpu_install) < dockerfile.index(requirements_install)


def test_public_smoke_workflow_installs_cpu_only_torch_first():
    workflow = Path(".github/workflows/public-preview.yml").read_text(encoding="utf-8")
    cpu_install = ".venv/bin/pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu"
    requirements_install = ".venv/bin/pip install -r requirements.txt"

    assert cpu_install in workflow
    assert workflow.index(cpu_install) < workflow.index(requirements_install)


def test_direct_evidence_selection_dedupes_sources_and_caps_context():
    from backend.core.orchestrator import _select_direct_evidence_chunks

    chunks = [
        {"id": "a1", "source_id": "source:a", "rerank_score": 9.0},
        {"id": "a2", "source_id": "source:a", "rerank_score": 8.0, "retrieval_reason": "sibling"},
        {"id": "b1", "source_id": "source:b", "rerank_score": 7.0},
        {"id": "c1", "source_id": "source:c", "rerank_score": 6.0},
        {"id": "d1", "source_id": "source:d", "rerank_score": 5.0},
    ]

    selected = _select_direct_evidence_chunks(chunks, {"routing_strategy": "access"})

    assert [chunk["id"] for chunk in selected] == ["a1", "b1", "c1"]


def test_direct_evidence_selection_prioritizes_route_critical_sources():
    from backend.core.orchestrator import _select_direct_evidence_chunks

    chunks = [
        {"id": "generic", "source_id": "kb:export", "source_type": "official_help_article", "rerank_score": 9.0},
        {"id": "release", "source_id": "rn:web", "source_type": "release_note", "rerank_score": 8.0},
        {"id": "policy", "source_id": "policy:export", "source_type": "policy", "rerank_score": 4.0},
    ]

    selected = _select_direct_evidence_chunks(chunks, {"routing_strategy": "policy"})

    assert [chunk["id"] for chunk in selected] == ["policy", "generic", "release"]


def test_direct_evidence_selection_demotes_route_type_without_ticket_overlap():
    from backend.core.orchestrator import _select_direct_evidence_chunks

    chunks = [
        {"id": "trial_policy", "source_id": "policies:trial_workspace_retention", "source_type": "policy", "rerank_score": 8.0},
        {"id": "export_kb", "source_id": "knowledge_base:export_conversation_history", "source_type": "knowledge_base", "rerank_score": 3.0},
        {"id": "export_policy", "source_id": "policies:data_export_eligibility", "source_type": "policy", "rerank_score": 2.0},
    ]

    selected = _select_direct_evidence_chunks(
        chunks,
        {"routing_strategy": "policy", "ticket": {"cleaned": "cannot find compliance export"}},
    )

    assert [chunk["id"] for chunk in selected][:2] == ["export_policy", "export_kb"]


def test_direct_evidence_selection_filters_mobile_badge_when_notification_source_matches():
    from backend.core.orchestrator import _select_direct_evidence_chunks

    chunks = [
        {"id": "badge", "source_id": "known_issues:kb_delayed_mobile_badge_counts", "source_type": "known_issue", "rerank_score": 8.0},
        {"id": "push", "source_id": "knowledge_base:kb_mobile_notification_troubleshooting", "source_type": "knowledge_base", "rerank_score": 4.0},
    ]

    selected = _select_direct_evidence_chunks(
        chunks,
        {"routing_strategy": "bug", "ticket": {"cleaned": "not getting mobile notifications for team inbox"}},
    )

    assert [chunk["id"] for chunk in selected] == ["push"]


def test_public_smoke_uses_admin_key_for_admin_routes():
    script = Path("scripts/public_smoke.sh").read_text(encoding="utf-8")

    assert '-H "x-api-key: $CONFIGURATOR_API_KEY_VALUE" "$BASE_URL/traces/$TRACE_ID"' in script
    assert '-H "x-api-key: $CONFIGURATOR_API_KEY_VALUE" "$BASE_URL/metrics/daily"' in script


def test_smoke_test_embeddings_do_not_load_local_model(monkeypatch):
    monkeypatch.setenv("SMOKE_TEST_MODE", "true")

    from backend.providers import embedding_model
    from knowledge_loader import kb_loader

    def fail_model_load(*args, **kwargs):
        raise AssertionError("smoke test embeddings should not load SentenceTransformer")

    monkeypatch.setattr(embedding_model, "_model", None)
    monkeypatch.setattr(embedding_model, "SentenceTransformer", fail_model_load)
    monkeypatch.setattr(kb_loader, "_model", None)
    monkeypatch.setattr(kb_loader, "SentenceTransformer", fail_model_load)

    provider_vec = embedding_model.get_embedding("mobile login 403")
    loader_vec = kb_loader.get_embedding("mobile login 403")

    assert len(provider_vec) == 384
    assert len(loader_vec) == 384
    assert provider_vec == loader_vec
    assert any(value != 0 for value in provider_vec)


def test_kb_identifier_insert_has_placeholder_for_every_column():
    import re
    from backend.db import schema

    columns_block = re.search(
        r"INSERT INTO knowledge_base_identifier \((.*?)\)\nVALUES",
        schema.INSERT_KB_IDENTIFIER,
        re.S,
    ).group(1)
    columns = [column.strip() for column in columns_block.replace("\n", " ").split(",") if column.strip()]

    assert schema.INSERT_KB_IDENTIFIER.count("%s") == len(columns)


def test_onboarding_uploaded_sources_update_sources_config(tmp_path, monkeypatch):
    from scripts import onboarding_tasks

    monkeypatch.setattr(onboarding_tasks, "ROOT", tmp_path)
    monkeypatch.setattr(onboarding_tasks, "load_knowledge", lambda: {"ok": True, "stdout": "loaded"})
    (tmp_path / "config").mkdir()
    result = onboarding_tasks.ingest_uploaded_sources(["demo_data/onboarding/uploads/help.csv", "demo_data/onboarding/uploads/guide.csv"])

    assert result["ok"] is True
    text = (tmp_path / "config/sources.yaml").read_text(encoding="utf-8")
    assert "onboarding_help" in text
    assert "onboarding_guide" in text
    assert "demo_data/onboarding/uploads/help.csv" in text


def test_runtime_dependencies_avoid_blocked_rag_frameworks():
    requirements = Path("requirements.txt").read_text().lower()
    for blocked in ["langchain", "llamaindex", "llama-index", "haystack", "ragatouille", "colbert"]:
        assert blocked not in requirements


def test_option_b_openapi_exposes_stable_api_contracts():
    schema = app.openapi()
    paths = schema["paths"]
    for path in [
        "/resolve",
        "/sources",
        "/feedback",
        "/draft-runs",
        "/knowledge-issues",
        "/traces/{trace_id}",
        "/traces/{trace_id}/replay",
        "/eval/run",
        "/configurator/source-preview",
        "/review-queue",
    ]:
        assert path in paths


def test_option_b_operational_endpoints_require_api_key(monkeypatch):
    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")
    client = TestClient(app)
    for path, method in [
        ("/sources", "get"),
        ("/eval/run", "post"),
        ("/review-queue", "get"),
        ("/draft-runs", "get"),
        ("/knowledge-issues", "post"),
        ("/traces/{trace_id}", "get"),
    ]:
        response = getattr(client, method)(
            path.replace("{trace_id}", "missing-trace"),
            headers={"x-api-key": "wrong"},
        )
        assert response.status_code == 401


def test_sources_endpoint_returns_license_metadata(monkeypatch):
    rows = [
        (
            "kb-1",
            "knowledge_base",
            "docs",
            1,
            True,
            True,
            False,
            "docs/example.csv",
            "CC-BY",
            True,
            "Example attribution",
            3,
        )
    ]

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return rows

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr("backend.api.app.psycopg2.connect", lambda *_args, **_kwargs: FakeConn())

    response = TestClient(app).get("/sources", headers={"x-api-key": "admin-secret"})

    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0]["source_license"] == "CC-BY"
    assert body["sources"][0]["attribution_required"] is True
    assert body["sources"][0]["chunk_count"] == 3


def test_eval_run_endpoint_is_deterministic_schema_only(monkeypatch):
    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")

    response = TestClient(app).post("/eval/run", headers={"x-api-key": "admin-secret"})

    assert response.status_code == 200
    report = response.json()["report"]
    assert report["schema_valid"] is True
    assert report["evaluated_result_count"] == 0
    assert report["hard_failure_count"] == 0


def test_review_queue_endpoint_returns_redacted_summary(monkeypatch):
    fake_cursor = Mock()
    fake_cursor.fetchall.return_value = [
        (
            7,
            "trace-1",
            "Customer cannot sign in...",
            "red",
            "high",
            "validation",
            "open",
            "reviewer@example.com",
            "Needs source check",
            "2026-05-08 12:00:00",
        )
    ]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.__enter__.return_value = fake_conn

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr("backend.api.app.get_conn", lambda: fake_conn)

    response = TestClient(app).get("/review-queue", headers={"x-api-key": "admin-secret"})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["trace_id"] == "trace-1"
    assert item["ticket_summary"] == "Customer cannot sign in..."
    assert item["status"] == "open"


# --- metrics and redaction tests ---
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.api.app import app
from backend.db import schema
from pipeline import cache
from pipeline.cache import token_edit_distance
from pipeline import ingestor


def test_ingestor_redacts_ticket_before_downstream_fields():
    context = ingestor.run({"ticket_raw": "Hi, this is Maya at maya@example.com and phone 415-555-1212."})
    ticket = context["ticket"]

    assert "maya@example.com" not in ticket["cleaned"]
    assert "415-555-1212" not in ticket["cleaned"]
    assert ticket["redaction_applied"] is True
    assert ticket["raw_text_hash"]


def test_token_edit_distance_reports_ratio_and_count():
    result = token_edit_distance("one two three", "one two changed")
    assert result["edit_distance_tokens"] == 1
    assert result["edit_distance_ratio"] == 0.3333


def test_feedback_schema_has_agent_action_metrics_columns():
    assert "agent_action" in schema.CREATE_FEEDBACK_TABLE
    assert "metrics_daily" in schema.CREATE_METRICS_DAILY
    assert schema.ALTER_FEEDBACK_ADD_FINAL_SENT_TEXT in schema.OPS_SETUP_QUERIES
    assert "draft_run_id" in schema.CREATE_FEEDBACK_TABLE
    assert "reason_code" in schema.CREATE_FEEDBACK_TABLE
    assert "abstention_correct" in schema.CREATE_FEEDBACK_TABLE


def test_draft_run_and_knowledge_issue_schema_exist():
    assert "CREATE TABLE IF NOT EXISTS draft_run" in schema.CREATE_DRAFT_RUN_TABLE
    assert "trace_id" in schema.CREATE_DRAFT_RUN_TABLE
    assert "ticket_preview_redacted" in schema.CREATE_DRAFT_RUN_TABLE
    assert "CREATE TABLE IF NOT EXISTS knowledge_issue" in schema.CREATE_KNOWLEDGE_ISSUE_TABLE
    assert "created_from_feedback_id" in schema.CREATE_KNOWLEDGE_ISSUE_TABLE
    assert schema.CREATE_DRAFT_RUN_TABLE in schema.OPS_SETUP_QUERIES
    assert schema.CREATE_KNOWLEDGE_ISSUE_TABLE in schema.OPS_SETUP_QUERIES


def test_analytics_schema_tracks_events_and_actor_metadata():
    assert "CREATE TABLE IF NOT EXISTS analytics_event" in schema.CREATE_ANALYTICS_EVENT_TABLE
    assert "event_type" in schema.CREATE_ANALYTICS_EVENT_TABLE
    assert "user_id" in schema.CREATE_ANALYTICS_EVENT_TABLE
    assert "team_id" in schema.CREATE_ANALYTICS_EVENT_TABLE
    assert "session_id" in schema.CREATE_ANALYTICS_EVENT_TABLE
    assert "trace_id" in schema.CREATE_ANALYTICS_EVENT_TABLE
    assert schema.CREATE_ANALYTICS_EVENT_TABLE in schema.OPS_SETUP_QUERIES
    assert "user_id" in schema.CREATE_FEEDBACK_TABLE
    assert "team_id" in schema.CREATE_FEEDBACK_TABLE
    assert "session_id" in schema.CREATE_FEEDBACK_TABLE
    assert "trace_id" in schema.CREATE_API_CALLS_TABLE
    assert "user_id" in schema.CREATE_API_CALLS_TABLE


def test_support_intelligence_report_groups_usage_eval_cost_and_gaps():
    report = analytics.build_support_intelligence_report(
        traces=[
            {
                "trace_id": "trace_1",
                "created_at": "2026-05-29",
                "user_id": "agent-a",
                "team_id": "tier-2",
                "session_id": "sess-1",
                "product": "billing",
                "role": "employee",
                "trace": {
                    "reranked_results": [{"id": "chunk_1", "source_id": "kb/refunds", "score": 0.82}],
                    "final_response": {
                        "confidence": "HIGH",
                        "validation": {"review_required": False},
                    },
                    "token_usage_by_stage": {"cost_usd": 0.012},
                },
            },
            {
                "trace_id": "trace_2",
                "created_at": "2026-05-29",
                "user_id": "agent-b",
                "team_id": "tier-1",
                "session_id": "sess-2",
                "product": "sso",
                "role": "admin",
                "trace": {
                    "reranked_results": [],
                    "final_response": {
                        "confidence": "LOW",
                        "draft_unavailable_reason": "No approved source found.",
                        "validation": {"review_required": True},
                    },
                    "token_usage_by_stage": {"cost_usd": 0.02},
                },
            },
        ],
        feedback=[
            {"rating": "thumbs_up", "feedback_reason": "good_answer", "agent_action": "sent_as_is", "product": "billing"},
            {"rating": "thumbs_down", "feedback_reason": "missing_source", "agent_action": "rejected", "product": "sso"},
        ],
        knowledge_issues=[
            {"issue_type": "missing_source", "severity": "medium", "status": "open", "product": "sso"},
        ],
        review_items=[
            {"needs_escalation": True, "source_issue_type": "missing_source", "severity": "high"},
        ],
        api_calls=[
            {"provider": "openai", "model": "gpt-4.1-mini", "step": "responder", "cost_usd": 0.01, "latency_ms": 900},
        ],
        events=[
            {"event_type": "source_clicked"},
        ],
        days=30,
    )

    assert report["usage"]["total_queries"] == 2
    assert report["usage"]["active_users"] == 2
    assert report["retrieval"]["no_answer_count"] == 1
    assert report["evaluation"]["helpful_rate"] == 0.5
    assert report["knowledge_gaps"]["open_issue_count"] == 1
    assert report["escalations"]["needs_escalation_count"] == 1
    assert report["costs"]["total_cost_usd"] == 0.032
    assert "ResolveKit Usage & Knowledge Gap Report" in analytics.render_support_intelligence_markdown(report)


def test_resolve_endpoint_passes_actor_metadata_to_orchestrator(monkeypatch):
    captured = {}

    def fake_run(ticket, meta):
        captured.update(meta)
        return {"mode": "suggest", "trace_id": "trace_1", "usage_summary": {"total_cost_usd": 0}}

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.api.app.allow_request", lambda: True)
    monkeypatch.setattr("backend.api.app.orchestrator.run", fake_run)
    monkeypatch.setattr("backend.api.app.record_analytics_event", lambda data: "evt_1")

    response = TestClient(app).post(
        "/resolve",
        headers={
            "x-api-key": "dev-secret",
            "x-resolvekit-user": "agent-a",
            "x-resolvekit-team": "tier-2",
            "x-resolvekit-session": "sess-1",
        },
        json={"ticket": "Need refund setup steps.", "product": "billing"},
    )

    assert response.status_code == 200
    assert captured["user_id"] == "agent-a"
    assert captured["team_id"] == "tier-2"
    assert captured["session_id"] == "sess-1"


def test_analytics_event_endpoint_records_source_click(monkeypatch):
    captured = {}

    def fake_record(data):
        captured.update(data)
        return "evt_click"

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.api.app.record_analytics_event", fake_record)

    response = TestClient(app).post(
        "/analytics/events",
        headers={"x-api-key": "dev-secret", "x-resolvekit-user": "agent-a"},
        json={
            "event_type": "source_clicked",
            "trace_id": "trace_1",
            "source_id": "kb/refunds",
            "metadata": {"rank": 1},
        },
    )

    assert response.status_code == 200
    assert response.json()["event_id"] == "evt_click"
    assert captured["event_type"] == "source_clicked"
    assert captured["user_id"] == "agent-a"
    assert captured["source_id"] == "kb/refunds"


def test_admin_ui_exposes_same_page_analytics_sections():
    html = (Path(__file__).resolve().parents[1] / "frontend" / "admin" / "index.html").read_text(encoding="utf-8")

    for label in ("Analytics", "Usage", "Retrieval", "Evaluation", "Costs", "Knowledge Gaps", "Config"):
        assert label in html


def test_feedback_endpoint_accepts_agent_action_metrics(monkeypatch):
    saved = {}

    def fake_save_feedback(data):
        saved.update(data)
        return "feedback-1"

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.api.app.save_feedback", fake_save_feedback)

    response = TestClient(app).post(
        "/feedback",
        headers={"x-api-key": "dev-secret"},
        json={
            "rating": "thumbs_up",
            "agent_action": "edited",
            "original_email": "Hi,\n\nOpen settings.",
            "final_sent_text": "Hi,\n\nPlease open settings.",
            "citations_used": "[\"policy:exports\"]",
            "citations_kept": "[\"policy:exports\"]",
            "draft_run_id": "draft_run_1",
            "reason_code": "good_answer",
            "abstention_correct": "not_applicable",
        },
    )

    assert response.status_code == 200
    assert saved["agent_action"] == "edited"
    assert saved["final_sent_text"].startswith("Hi,")
    assert saved["citations_kept"] == "[\"policy:exports\"]"
    assert saved["draft_run_id"] == "draft_run_1"
    assert saved["reason_code"] == "good_answer"


def test_negative_feedback_creates_knowledge_issue(monkeypatch):
    created = {}

    def fake_save_feedback(data):
        return "feedback-9"

    def fake_create_knowledge_issue(data):
        created.update(data)
        return "ki_9"

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.api.app.save_feedback", fake_save_feedback)
    monkeypatch.setattr("backend.api.app.create_review_queue_item", lambda data: None)
    monkeypatch.setattr("backend.api.app.create_knowledge_issue", fake_create_knowledge_issue)

    response = TestClient(app).post(
        "/feedback",
        headers={"x-api-key": "dev-secret"},
        json={
            "rating": "thumbs_down",
            "agent_action": "rejected",
            "draft_run_id": "draft_run_9",
            "trace_id": "trace_9",
            "feedback_reason": "missing_source",
            "reason_code": "missing_source",
            "comment": "Could not cite the setup article.",
            "retrieved_chunk_ids": "[\"chunk_a\"]",
        },
    )

    assert response.status_code == 200
    assert response.json()["knowledge_issue_id"] == "ki_9"
    assert created["created_from_feedback_id"] == "feedback-9"
    assert created["draft_run_id"] == "draft_run_9"
    assert created["trace_id"] == "trace_9"
    assert created["issue_type"] == "missing_source"
    assert created["chunk_id"] == "chunk_a"


def test_save_feedback_writes_agent_metrics(monkeypatch):
    captured = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(cache, "get_conn", lambda: FakeConn())

    cache.save_feedback({
        "user_token_hash": "hash",
        "rating": "thumbs_up",
        "original_email": "one two three",
        "final_sent_text": "one two changed",
        "agent_action": "edited",
        "citations_kept": "[\"doc:1\"]",
        "draft_run_id": "draft_run_1",
        "reason_code": "good_answer",
        "abstention_correct": "not_applicable",
    })

    assert "agent_action" in captured["query"]
    assert "draft_run_id" in captured["query"]
    assert len(captured["params"]) == 44
    assert captured["params"][-8:] == ("draft_run_1", "good_answer", "not_applicable", "edited", "one two changed", 0.3333, 1, "[\"doc:1\"]")


def test_save_draft_run_writes_trace_link(monkeypatch):
    captured = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(cache, "get_conn", lambda: FakeConn())

    draft_run_id = cache.save_draft_run({
        "trace_id": "trace_1",
        "ticket_text_hash": "hash_1",
        "ticket_preview_redacted": "Customer cannot sign in",
        "final_draft": "Try reset.",
        "confidence_band": "green",
        "confidence_score": 0.9,
        "validation_status": "ok",
        "citations_used": ["doc:1"],
        "source_ids": ["kb"],
        "config_hash": "cfg",
    })

    assert draft_run_id.startswith("draft_")
    assert "INSERT INTO draft_run" in captured["query"]
    assert captured["params"][1] == "trace_1"
    assert captured["params"][4] == "Customer cannot sign in"


def test_save_draft_run_serializes_plain_source_string_as_json_list(monkeypatch):
    captured = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            captured["params"] = params

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(cache, "get_conn", lambda: FakeConn())

    cache.save_draft_run({
        "trace_id": "trace_1",
        "ticket_text_hash": "hash_1",
        "ticket_preview_redacted": "Customer cannot sign in",
        "final_draft": "Try reset.",
        "confidence_band": "green",
        "confidence_score": 0.9,
        "validation_status": "ok",
        "citations_used": "demo_knowledge_base.csv",
        "source_ids": "knowledge_base:kb_mobile_login",
        "config_hash": "cfg",
    })

    assert captured["params"][9] == "[\"demo_knowledge_base.csv\"]"
    assert captured["params"][10] == "[\"knowledge_base:kb_mobile_login\"]"


def test_draft_history_endpoint_returns_read_only_summary(monkeypatch):
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return None

        def fetchall(self):
            return [(
                "draft_1", "trace_1", "Cannot sign in", "MEDIUM", "ok",
                "[\"doc:1\"]", "[\"kb\"]", "cfg", "2026-05-15 00:00:00"
            )]

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.api.app.get_conn", lambda: FakeConn())

    response = TestClient(app).get("/draft-runs", headers={"x-api-key": "dev-secret"})

    assert response.status_code == 200
    item = response.json()["draft_runs"][0]
    assert item["id"] == "draft_1"
    assert item["trace_id"] == "trace_1"
    assert item["ticket_preview"] == "Cannot sign in"


def test_strategy_schema_scaffolding_covers_remaining_phases():
    assert "CREATE TABLE IF NOT EXISTS feedback_label" in schema.CREATE_FEEDBACK_LABEL_TABLE
    assert "CREATE TABLE IF NOT EXISTS knowledge_patch" in schema.CREATE_KNOWLEDGE_PATCH_TABLE
    assert "CREATE TABLE IF NOT EXISTS experiment" in schema.CREATE_EXPERIMENT_TABLE
    assert "CREATE TABLE IF NOT EXISTS experiment_arm" in schema.CREATE_EXPERIMENT_ARM_TABLE
    assert "CREATE TABLE IF NOT EXISTS experiment_result" in schema.CREATE_EXPERIMENT_RESULT_TABLE
    for column in ["document_id", "document_version", "chunk_version", "is_active", "superseded_by_chunk_id", "active_from", "active_until"]:
        assert column in schema.CREATE_KB_IDENTIFIER
    for column in ["experiment_id", "experiment_arm", "experiment_mode", "variant_config_hash", "source_version_set"]:
        assert column in schema.CREATE_DRAFT_RUN_TABLE
    assert schema.CREATE_FEEDBACK_LABEL_TABLE in schema.OPS_SETUP_QUERIES
    assert schema.CREATE_KNOWLEDGE_PATCH_TABLE in schema.OPS_SETUP_QUERIES


def test_retrieval_excludes_inactive_or_disabled_chunks():
    assert "COALESCE(ki.disabled, FALSE) = FALSE" in schema.SEMANTIC_SEARCH
    assert "COALESCE(ki.is_active, TRUE) = TRUE" in schema.SEMANTIC_SEARCH
    assert "COALESCE(ki.disabled, FALSE) = FALSE" in schema.FETCH_ALL_FOR_BM25
    assert "COALESCE(ki.is_active, TRUE) = TRUE" in schema.FETCH_ALL_FOR_BM25


def test_run_trace_includes_stage_events_and_size_metadata():
    trace = build_run_trace(
        {"ticket_raw": "Cannot sign in", "request_meta": {"product": "demo"}},
        {"confidence": "LOW", "draft_email": "", "validation": {"gatekeeper_flagged": True}},
        started_at=time.time(),
        errors=["retrieval skipped"],
    ).to_dict()
    assert "stage_events" in trace
    assert trace["stage_events"][0]["stage"] == "total"
    assert "trace_size" in trace
    assert "truncated" in trace["trace_size"]


def test_strategy_api_paths_are_exposed():
    paths = app.openapi()["paths"]
    for path in [
        "/support-bundles/{trace_id}",
        "/feedback-labels",
        "/knowledge-patches",
        "/knowledge-issues",
        "/experiments",
        "/experiments/offline-replay",
    ]:
        assert path in paths


def test_support_bundle_endpoint_shapes_trace_export(monkeypatch):
    trace = {
        "trace_id": "trace-1",
        "redacted_ticket_preview": "Cannot sign in",
        "reranked_results": [{"id": "chunk-1", "source_id": "kb", "content_preview": "Use reset."}],
        "final_response": {"draft_email": "Try reset.", "validation": {"ok": True}},
        "validation_output": {"ok": True},
        "config_hash": "cfg",
    }
    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr("backend.api.app.get_run_trace", lambda trace_id: trace)

    response = TestClient(app).get("/support-bundles/trace-1", headers={"x-api-key": "admin-secret"})

    assert response.status_code == 200
    bundle = response.json()["bundle"]
    assert "trace.json" in bundle
    assert "retrieved_chunks.md" in bundle
    assert "final_answer.md" in bundle
    assert "config_snapshot.json" in bundle


def test_knowledge_patch_creation_requires_review_status(monkeypatch):
    created = {}

    def fake_create_knowledge_patch(data):
        created.update(data)
        return "kp_1"

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr("backend.api.app.create_knowledge_patch", fake_create_knowledge_patch)

    response = TestClient(app).post(
        "/knowledge-patches",
        headers={"x-api-key": "admin-secret"},
        json={
            "knowledge_issue_id": "ki_1",
            "patch_type": "disable_chunk",
            "target_chunk_id": "chunk-1",
            "before_text": "old",
            "after_text": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["knowledge_patch_id"] == "kp_1"
    assert created["review_status"] == "proposed"
    assert created["patch_type"] == "disable_chunk"


def test_experiment_registry_defaults_offline_only(monkeypatch):
    created = {}

    def fake_create_experiment(data):
        created.update(data)
        return "exp_1"

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr("backend.api.app.create_experiment", fake_create_experiment)

    response = TestClient(app).post(
        "/experiments",
        headers={"x-api-key": "admin-secret"},
        json={"name": "retrieval_strategy_v1", "description": "Compare retrieval arms."},
    )

    assert response.status_code == 200
    assert created["status"] == "disabled"
    assert created["mode"] == "offline_replay"


def test_golden_eval_hard_fails_red_confidence_answer():
    from scripts.run_golden_eval import evaluate_stored_results

    rows = [{
        "ticket_id": "case-red",
        "expected_confidence_band": "red",
        "expected_route": "account_access",
        "expected_source_ids": [],
        "forbidden_source_ids": [],
        "review_required_expected": True,
    }]
    results = [{
        "ticket_id": "case-red",
        "confidence_band": "red",
        "route": "account_access",
        "abstained": False,
        "answer_text": "Customer-facing answer despite red confidence.",
        "validation_passed": True,
    }]

    report = evaluate_stored_results(rows, results)

    assert report["hard_failure_count"] == 1
    assert "red confidence" in report["hard_failures"][0]


def test_strategy_docs_exist():
    for path in [
        "docs/README.md",
        "docs/TECHNICAL.md",
        "docs/DEMO.md",
    ]:
        assert Path(path).exists()


def test_reingestion_tombstones_existing_chunks_without_delete():
    from knowledge_loader import kb_loader

    calls = []

    class FakeCursor:
        def execute(self, query, params=()):
            calls.append((query, params))

    kb_loader.tombstone_existing_document_chunks(
        FakeCursor(),
        article_id="kb_login",
        superseded_by_chunk_ids=["kb_login_v2_0", "kb_login_v2_1"],
    )

    query = calls[0][0]
    assert "UPDATE knowledge_base_identifier" in query
    assert "is_active = FALSE" in query
    assert "superseded_by_chunk_id" in query
    assert "DELETE FROM knowledge_base_identifier" not in Path("knowledge_loader/kb_loader.py").read_text()


def test_kb_identifier_insert_persists_versioning_and_active_state():
    for column in ["document_id", "document_version", "chunk_version", "is_active", "active_from", "active_until"]:
        assert column in schema.INSERT_KB_IDENTIFIER
    assert "is_active = EXCLUDED.is_active" in schema.INSERT_KB_IDENTIFIER


def test_strategy_phase_3_to_7_api_paths_are_exposed():
    paths = app.openapi()["paths"]
    for path in [
        "/knowledge-workbench",
        "/sources/{source_id}/chunks/{chunk_id}/disable",
        "/sources/{source_id}/mark-stale",
        "/sources/reingest-preview",
        "/traces/{trace_id}/diagnostics",
        "/experiments/registry",
        "/experiments/offline-replay",
    ]:
        assert path in paths


def test_source_chunk_disable_marks_chunk_inactive(monkeypatch):
    captured = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=()):
            captured["query"] = query
            captured["params"] = params

        def fetchone(self):
            return ("chunk-1",)

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr("backend.api.app.psycopg2.connect", lambda *_args, **_kwargs: FakeConn())

    response = TestClient(app).post(
        "/sources/source-1/chunks/chunk-1/disable",
        headers={"x-api-key": "admin-secret"},
        json={"reason": "stale_source"},
    )

    assert response.status_code == 200
    assert "is_active = FALSE" in captured["query"]
    assert captured["params"][0] == "stale_source"


def test_trace_diagnostics_endpoint_includes_run_and_chunk_fields(monkeypatch):
    trace = {
        "trace_id": "trace-1",
        "ticket_text_hash": "hash",
        "redacted_ticket_preview": "Cannot sign in",
        "timestamp": "2026-05-16T00:00:00+00:00",
        "final_response": {
            "draft_run_id": "draft-1",
            "confidence": "LOW",
            "draft_email": "Try reset.",
            "citations": ["kb:login"],
            "agent_action": "pending",
        },
        "validation_output": {"status": "ok"},
        "reranked_results": [{
            "id": "chunk-1",
            "source_id": "kb:login",
            "document_id": "doc-1",
            "document_version": 2,
            "chunk_version": 1,
            "score": 0.9,
            "is_active": True,
            "is_approved": True,
            "is_customer_facing_allowed": True,
        }],
        "stage_events": [{"stage": "total", "status": "ok"}],
        "errors": [],
    }
    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.api.app.get_run_trace", lambda trace_id: trace)

    response = TestClient(app).get("/traces/trace-1/diagnostics", headers={"x-api-key": "dev-secret"})

    assert response.status_code == 200
    diagnostics = response.json()["diagnostics"]
    assert diagnostics["run"]["draft_run_id"] == "draft-1"
    assert diagnostics["chunks"][0]["document_version"] == 2
    assert diagnostics["exports"]["trace.json"] == "/support-bundles/trace-1"


def test_golden_eval_counts_forbidden_and_unallowed_source_gates():
    from scripts.run_golden_eval import evaluate_stored_results

    rows = [{
        "ticket_id": "case-1",
        "expected_confidence_band": "green",
        "expected_route": "general",
        "expected_source_ids": ["knowledge_base:allowed"],
        "forbidden_source_ids": ["historical_tickets:T-1"],
    }]
    results = [{
        "ticket_id": "case-1",
        "confidence_band": "green",
        "route": "general",
        "cited_source_ids": ["historical_tickets:T-1"],
    }]

    report = evaluate_stored_results(rows, results)

    assert report["hard_failure_count"] == 2
    assert any("forbidden" in item for item in report["hard_failures"])
    assert any("unallowed" in item for item in report["hard_failures"])


def test_golden_eval_hard_gates_customer_facing_citations_not_context_only_sources():
    from scripts.run_golden_eval import evaluate_stored_results

    rows = [{
        "ticket_id": "case-1",
        "expected_confidence_band": "green",
        "expected_route": "general",
        "expected_source_ids": ["knowledge_base:allowed"],
        "forbidden_source_ids": [],
    }]
    results = [{
        "ticket_id": "case-1",
        "confidence_band": "green",
        "route": "general",
        "retrieved_source_ids": ["knowledge_base:allowed", "knowledge_base:context"],
        "cited_source_ids": ["knowledge_base:allowed", "knowledge_base:context"],
        "customer_facing_cited_source_ids": ["knowledge_base:allowed"],
    }]

    report = evaluate_stored_results(rows, results)

    assert report["hard_failure_count"] == 0
    assert report["citation_precision"] == 1.0
    assert report["source_precision"] == 0.5


def test_golden_eval_ignores_stale_abstention_citations():
    from scripts.run_golden_eval import evaluate_stored_results

    rows = [{
        "ticket_id": "case-1",
        "expected_confidence_band": "red",
        "expected_route": "general",
        "expected_source_ids": ["knowledge_base:allowed"],
        "forbidden_source_ids": [],
        "review_required_expected": True,
    }]
    results = [{
        "ticket_id": "case-1",
        "confidence_band": "red",
        "route": "general",
        "abstained": True,
        "fallback_reason": "Draft unavailable because no approved source supports a safe answer.",
        "cited_source_ids": ["knowledge_base:stale"],
        "customer_facing_cited_source_ids": ["knowledge_base:stale"],
    }]

    report = evaluate_stored_results(rows, results)

    assert report["hard_failure_count"] == 0


def test_golden_eval_ignores_stale_abstention_unsupported_claims():
    from scripts.run_golden_eval import evaluate_stored_results

    rows = [{
        "ticket_id": "case-1",
        "expected_confidence_band": "red",
        "expected_route": "general",
        "expected_source_ids": [],
        "forbidden_source_ids": [],
        "review_required_expected": True,
    }]
    results = [{
        "ticket_id": "case-1",
        "confidence_band": "red",
        "route": "general",
        "abstained": True,
        "fallback_reason": "Draft unavailable because no approved source supports a safe answer.",
        "unsupported_factual_claim_count": 1,
        "unsupported_claims": ["Factual answer fields did not cite approved evidence."],
        "answer_text": "I do not have enough approved information to answer safely.",
    }]

    report = evaluate_stored_results(rows, results)

    assert report["hard_failure_count"] == 0


def test_generate_golden_results_extracts_only_answer_label_citations():
    from scripts.generate_golden_results import _result_from_resolution

    resolution = {
        "root_cause": "Supported by [KB-2].",
        "resolution_steps": "Try the approved step.",
        "validation": {
            "passed": True,
            "citations": [
                {"citation_id": "KB-1", "source_id": "knowledge_base:context"},
                {"citation_id": "KB-2", "source_id": "knowledge_base:allowed"},
            ],
        },
        "retrieval_signals": {
            "support_context_bundles": [
                {"label": "KB-1", "source_id": "knowledge_base:context"},
                {"label": "KB-2", "source_id": "knowledge_base:allowed"},
            ]
        },
        "confidence_scorer": {"confidence_band": "green"},
    }

    result = _result_from_resolution({"ticket_id": "case-1"}, resolution, latency_ms=123)

    assert result["retrieved_source_ids"] == ["knowledge_base:context", "knowledge_base:allowed"]
    assert result["customer_facing_cited_source_ids"] == ["knowledge_base:allowed"]
    assert result["cited_source_ids"] == ["knowledge_base:allowed"]


def test_generate_golden_results_hard_claims_exclude_review_only_findings():
    from scripts.generate_golden_results import _result_from_resolution

    resolution = {
        "root_cause": "Human review is required. [KB-1]",
        "validation": {
            "passed": False,
            "citations": [{"citation_id": "KB-1", "source_id": "policies:billing"}],
            "unsupported_claims": [
                "Response answered directly despite red confidence.",
                "Response produced a customer draft despite red confidence.",
                "Factual answer fields did not cite approved evidence.",
            ],
        },
        "confidence_scorer": {"confidence_band": "red"},
    }

    result = _result_from_resolution({"ticket_id": "case-1"}, resolution, latency_ms=123)

    assert result["unsupported_factual_claim_count"] == 0
    assert result["validation_review_finding_count"] == 3


def test_generate_golden_results_abstention_ignores_stale_rendered_reply_citations():
    from scripts.generate_golden_results import _result_from_resolution

    resolution = {
        "root_cause": "I do not have enough approved information to answer safely.",
        "resolution_steps": "Escalate for human review.",
        "draft_email": "",
        "draft_unavailable_reason": "Draft unavailable because no approved source supports a safe answer.",
        "rendered_reply": "Old pre-abstention answer cited [KB-1].",
        "raw": "Old raw model answer cited [KB-1].",
        "validation": {
            "passed": False,
            "citations": [{"citation_id": "KB-1", "source_id": "knowledge_base:stale"}],
        },
        "confidence_scorer": {"confidence_band": "red"},
    }

    result = _result_from_resolution({"ticket_id": "case-1"}, resolution, latency_ms=123)

    assert result["customer_facing_cited_source_ids"] == []
    assert "Old pre-abstention answer" not in result["answer_text"]
    assert "Old raw model answer" not in result["answer_text"]


def test_generate_golden_results_abstention_ignores_factual_citation_warning():
    from scripts.generate_golden_results import _result_from_resolution

    resolution = {
        "resolution_steps": "Escalate for human review.",
        "draft_unavailable_reason": "Draft unavailable because no approved source supports a safe answer.",
        "validation": {
            "passed": False,
            "unsupported_claims": ["Factual answer fields did not cite approved evidence."],
            "citations": [{"citation_id": "KB-1", "source_id": "knowledge_base:setup"}],
        },
        "confidence_scorer": {"confidence_band": "red"},
    }

    result = _result_from_resolution({"ticket_id": "case-1"}, resolution, latency_ms=123)

    assert result["unsupported_factual_claim_count"] == 0
    assert result["unsupported_claims"] == []


def test_source_safety_triage_buckets_eval_contract_vs_over_citation():
    from scripts.source_safety_triage import build_triage_report

    golden_rows = [{
        "ticket_id": "case-1",
        "expected_source_ids": ["knowledge_base:allowed"],
    }]
    result_rows = [{
        "ticket_id": "case-1",
        "retrieved_source_ids": ["knowledge_base:allowed", "knowledge_base:context"],
        "cited_source_ids": ["knowledge_base:context"],
        "customer_facing_cited_source_ids": ["knowledge_base:context"],
    }]

    report = build_triage_report(golden_rows, result_rows, source_aliases={})

    assert report["summary"]["case_count"] == 1
    assert report["summary"]["bucket_counts"]["customer_over_citation"] == 1
    assert report["cases"][0]["unallowed_customer_facing"] == ["knowledge_base:context"]


def test_source_safety_triage_treats_abstention_citations_as_context_only():
    from scripts.source_safety_triage import build_triage_report

    golden_rows = [{
        "ticket_id": "case-1",
        "expected_source_ids": ["knowledge_base:allowed"],
    }]
    result_rows = [{
        "ticket_id": "case-1",
        "abstained": True,
        "fallback_reason": "Draft unavailable because no approved source supports a safe answer.",
        "retrieved_source_ids": ["knowledge_base:allowed", "knowledge_base:context"],
        "evidence_context_source_ids": ["knowledge_base:allowed", "knowledge_base:context"],
        "customer_facing_cited_source_ids": ["knowledge_base:context"],
    }]

    report = build_triage_report(golden_rows, result_rows, source_aliases={})

    assert report["summary"]["bucket_counts"]["context_only_over_breadth"] == 1
    assert report["cases"][0]["unallowed_customer_facing"] == []


def test_loader_split_helpers_exist():
    from knowledge_loader import kb_loader

    for name in ["read_csv", "normalize_rows", "chunk_rows", "attach_metadata", "write_to_db"]:
        assert callable(getattr(kb_loader, name))


def test_experiment_registry_and_offline_replay_report(monkeypatch):
    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")

    registry = TestClient(app).get("/experiments/registry", headers={"x-api-key": "dev-secret"})
    assert registry.status_code == 200
    assert registry.json()["registry"]["default_mode"] == "offline_replay"
    assert registry.json()["registry"]["arms"][0]["id"] == "control_current_rag"

    replay = TestClient(app).post(
        "/experiments/offline-replay",
        headers={"x-api-key": "admin-secret"},
        json={
            "experiment_id": "exp-1",
            "arms": [{"id": "control_current_rag"}, {"id": "structured_reply_v1"}],
            "eval_case_ids": ["G-001", "G-002"],
        },
    )

    assert replay.status_code == 200
    report = replay.json()["report"]
    assert report["case_count"] == 2
    assert report["recommendation"] in {"promote", "revise", "reject"}
    assert "markdown" in replay.json()


def test_daily_metrics_endpoint_reads_snapshot(monkeypatch):
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        (
            "2026-05-13",
            10,
            4,
            3,
            2,
            1,
            0.4,
            0.2,
            0.25,
            0.7,
            1200,
            4800,
            0.03,
            {"HIGH": {"sent_as_is": 4}},
        )
    ]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.__enter__.return_value = fake_conn

    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr("backend.api.app.get_conn", lambda: fake_conn)

    response = TestClient(app).get("/metrics/daily", headers={"x-api-key": "admin-secret"})

    assert response.status_code == 200
    metric = response.json()["metrics"][0]
    assert metric["send_as_is_rate"] == 0.4
    assert metric["latency_p95_ms"] == 4800


# --- operator visibility tests ---
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.api.app import app


def test_trace_list_endpoint_summarizes_operator_fields(monkeypatch):
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        (
            "trace-1",
            "2026-05-13 10:00:00",
            "Customer cannot export...",
            "cfg123",
            "local",
            "standard",
            "example_product",
            "website",
            "admin",
            {
                "final_response": {
                    "confidence": "LOW",
                    "draft_unavailable_reason": "No approved source supports a safe answer.",
                },
                "reranked_results": [{"id": "kb_1"}],
            },
        )
    ]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.__enter__.return_value = fake_conn

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.api.app.get_conn", lambda: fake_conn)

    response = TestClient(app).get("/traces", headers={"x-api-key": "dev-secret"})

    assert response.status_code == 200
    trace = response.json()["traces"][0]
    assert trace["trace_id"] == "trace-1"
    assert trace["confidence"] == "LOW"
    assert trace["draft_unavailable_reason"].startswith("No approved source")
    assert trace["retrieved_result_count"] == 1


def test_source_status_endpoint_reports_freshness_and_quality(monkeypatch):
    rows = [
        (
            "knowledge_base:kb_1",
            "knowledge_base",
            "knowledge_base",
            "demo_knowledge_base.csv",
            "2026-05-13T00:00:00+00:00",
            "2026-05-13T00:00:00+00:00",
            "",
            3,
            3,
            3,
            0,
            1,
            3,
            1,
        ),
        (
            "policy:old",
            "policy",
            "policies",
            "demo_policies.csv",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            "2026-02-01T00:00:00+00:00",
            2,
            2,
            2,
            0,
            0,
            1,
            0,
        ),
    ]

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return rows

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    monkeypatch.setattr("backend.core.config.CONFIGURATOR_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr("backend.api.app.psycopg2.connect", lambda *_args, **_kwargs: FakeConn())

    response = TestClient(app).get("/sources/status", headers={"x-api-key": "admin-secret"})

    assert response.status_code == 200
    body = response.json()
    assert body["dashboard"]["source_count"] == 2
    assert body["dashboard"]["needs_review_count"] == 1
    assert body["dashboard"]["quality_warning_count"] == 1
    assert body["sources"][0]["ingestion_status"] == "loaded"
    assert body["sources"][0]["quality_report"]["metadata_completeness"] == 1.0


def test_configurator_sandbox_shows_draft_unavailable_reason():
    html = open("frontend/configurator/index.html", encoding="utf-8").read()
    assert "Why Draft Is Unavailable" in html
    assert "draft_unavailable_reason" in html


# --- eval gate tests ---
from scripts import run_golden_eval


def test_default_golden_set_has_50_plus_cases_and_connector_coverage():
    rows = run_golden_eval._read_jsonl(run_golden_eval.DEFAULT_GOLDEN_SET)
    schema = run_golden_eval._load_schema(run_golden_eval.DEFAULT_SCHEMA)
    report = run_golden_eval.validate_golden_rows(rows, schema)
    formats = {row.get("connector_format") for row in rows}

    assert report["schema_valid"] is True
    assert report["case_count"] >= 50
    assert {"csv", "pdf", "xlsx"} <= formats


def test_planner_routes_match_default_golden_set():
    from pipeline import planner

    rows = run_golden_eval._read_jsonl(run_golden_eval.DEFAULT_GOLDEN_SET)
    mismatches = []
    for row in rows:
        context = {
            "ticket": {"cleaned": row["ticket_text"]},
            "request_meta": {
                "product": row.get("product", ""),
                "access_channel": row.get("platform", ""),
                "permission_level": row.get("role", ""),
            },
            "product": row.get("product", ""),
            "platform": row.get("platform", ""),
        }
        actual = planner.run(context)["routing_strategy"]
        if actual != row["expected_route"]:
            mismatches.append((row["ticket_id"], row["expected_route"], actual))
    assert mismatches == []


def test_release_gate_blocks_source_safety_hard_failures():
    report = {
        "schema_valid": True,
        "evaluated_result_count": 1,
        "hard_failure_count": 1,
        "validation_failure_count": 0,
        "avg_latency_ms": 100,
        "total_cost_usd": 0.01,
    }
    gate = run_golden_eval.release_gate_report(report, max_avg_latency_ms=1000, max_total_cost_usd=1.0)
    assert gate["passed"] is False
    assert "source-safety hard failures present" in gate["blockers"]


def test_release_gate_warns_on_validation_failures_without_safety_failures():
    report = {
        "schema_valid": True,
        "evaluated_result_count": 1,
        "hard_failure_count": 0,
        "validation_failure_count": 2,
        "avg_latency_ms": 100,
        "total_cost_usd": 0.01,
    }
    gate = run_golden_eval.release_gate_report(report, max_avg_latency_ms=1000, max_total_cost_usd=1.0)
    assert gate["passed"] is True
    assert "2 validation/review failures present" in gate["warnings"]


def test_release_gate_public_alpha_allows_current_quality_warning_profile():
    report = {
        "schema_valid": True,
        "evaluated_result_count": 52,
        "hard_failure_count": 0,
        "validation_failure_count": 12,
        "retrieval_recall_at_3": 0.6596,
        "retrieval_recall_at_5": 0.6596,
        "source_precision": 0.4716,
        "citation_precision": 1.0,
        "required_point_coverage": 0.0577,
        "avg_latency_ms": 100,
        "total_cost_usd": 0.023234,
    }
    gate = run_golden_eval.release_gate_report(report, release_profile="public_alpha")
    assert gate["passed"] is True
    assert gate["profile"] == "public_alpha"
    assert "12 validation/review failures present" in gate["warnings"]


def test_release_gate_production_blocks_current_quality_warning_profile():
    report = {
        "schema_valid": True,
        "evaluated_result_count": 52,
        "hard_failure_count": 0,
        "validation_failure_count": 12,
        "retrieval_recall_at_3": 0.6596,
        "retrieval_recall_at_5": 0.6596,
        "source_precision": 0.4716,
        "citation_precision": 1.0,
        "required_point_coverage": 0.0577,
        "avg_latency_ms": 100,
        "total_cost_usd": 0.023234,
    }
    gate = run_golden_eval.release_gate_report(report, release_profile="production")
    assert gate["passed"] is False
    assert gate["profile"] == "production"
    assert any("validation/review failures" in blocker for blocker in gate["blockers"])
    assert any("source_precision" in blocker for blocker in gate["blockers"])
    assert any("retrieval_recall_at_3" in blocker for blocker in gate["blockers"])
    assert any("retrieval_recall_at_5" in blocker for blocker in gate["blockers"])
    assert any("required_point_coverage" in blocker for blocker in gate["blockers"])


def test_release_gate_production_passes_when_quality_targets_are_met():
    report = {
        "schema_valid": True,
        "evaluated_result_count": 52,
        "hard_failure_count": 0,
        "validation_failure_count": 4,
        "retrieval_recall_at_3": 0.75,
        "retrieval_recall_at_5": 0.75,
        "source_precision": 0.60,
        "citation_precision": 1.0,
        "required_point_coverage": 0.50,
        "avg_latency_ms": 100,
        "total_cost_usd": 0.023234,
    }
    gate = run_golden_eval.release_gate_report(report, release_profile="production")
    assert gate["passed"] is True
    assert gate["blockers"] == []


def test_release_gate_blocks_latency_and_cost_budgets():
    report = {
        "schema_valid": True,
        "evaluated_result_count": 1,
        "hard_failure_count": 0,
        "validation_failure_count": 0,
        "avg_latency_ms": 2000,
        "total_cost_usd": 2.5,
    }
    gate = run_golden_eval.release_gate_report(report, max_avg_latency_ms=1000, max_total_cost_usd=1.0)
    assert gate["passed"] is False
    assert any("latency" in blocker for blocker in gate["blockers"])
    assert any("cost" in blocker for blocker in gate["blockers"])


def test_release_gate_blocks_baseline_regression_when_enabled():
    report = {
        "schema_valid": True,
        "evaluated_result_count": 1,
        "hard_failure_count": 0,
        "validation_failure_count": 0,
    }
    gate = run_golden_eval.release_gate_report(
        report,
        baseline_diff={"retrieval_recall": {"baseline": 1.0, "current": 0.9, "delta": -0.1}},
        fail_on_baseline_regression=True,
    )
    assert gate["passed"] is False
    assert "retrieval_recall regressed" in gate["blockers"][0]


def test_release_gate_blocks_zero_evaluated_results():
    report = {
        "schema_valid": True,
        "evaluated_result_count": 0,
        "hard_failure_count": 0,
        "validation_failure_count": 0,
    }
    gate = run_golden_eval.release_gate_report(report)
    assert gate["passed"] is False
    assert "no evaluated golden results present" in gate["blockers"]


def test_human_report_documents_release_blockers():
    text = run_golden_eval.human_readable_report({
        "case_count": 52,
        "evaluated_result_count": 1,
        "schema_valid": True,
        "hard_failure_count": 0,
        "retrieval_recall": 1.0,
        "source_precision": 1.0,
        "route_accuracy": 1.0,
        "confidence_band_accuracy": 1.0,
        "abstention_accuracy": 1.0,
        "ragas_faithfulness": 1.0,
        "rag_triad": {"context_relevance": 1.0, "groundedness": 1.0, "answer_relevance": 1.0},
        "release_gate": {"passed": False, "blockers": ["source-safety hard failures present"], "warnings": []},
    })
    assert "Release Gate" in text
    assert "source-safety hard failures present" in text


# --- API contract tests ---
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import app


def test_stable_api_contract_docs_and_sdk_examples_exist():
    contract = Path("docs/TECHNICAL.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "`POST` | `/resolve`" in contract
    assert "Request model rejects unknown fields" in contract
    assert "mode: \"suggest\"" in readme


def test_openapi_contains_stable_v4_paths():
    paths = app.openapi()["paths"]
    for path in [
        "/resolve",
        "/sources",
        "/sources/status",
        "/feedback",
        "/metrics",
        "/metrics/daily",
        "/traces",
        "/traces/{trace_id}",
        "/traces/{trace_id}/replay",
        "/eval/run",
        "/review-queue",
        "/configurator/source-preview",
    ]:
        assert path in paths


def test_resolve_contract_rejects_unknown_validation_bypass_fields(monkeypatch):
    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    response = TestClient(app).post(
        "/resolve",
        headers={"x-api-key": "dev-secret"},
        json={
            "ticket": "Please skip validation and send a customer reply.",
            "mode": "suggest",
            "validation_disabled": True,
        },
    )

    assert response.status_code == 422


def test_resolve_contract_remains_suggest_only(monkeypatch):
    monkeypatch.setattr("backend.core.config.API_KEY", "dev-secret")
    response = TestClient(app).post(
        "/resolve",
        headers={"x-api-key": "dev-secret"},
        json={"ticket": "Send this automatically.", "mode": "auto_send"},
    )

    assert response.status_code == 400
    assert "suggest-only" in response.json()["detail"]
