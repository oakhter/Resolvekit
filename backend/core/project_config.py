from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from knowledge_loader.connectors import ConnectorError, SourceDocument, get_connector_for_path

try:
    import yaml
except Exception:  # pragma: no cover - fallback for fresh environments before PyYAML install
    yaml = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

LOCAL_FILES = {
    "products": CONFIG_DIR / "products.yaml",
    "sources": CONFIG_DIR / "sources.yaml",
    "output": CONFIG_DIR / "output.yaml",
    "retrieval_policy": CONFIG_DIR / "retrieval_policy.yaml",
    "workflow": CONFIG_DIR / "workflow.yaml",
}

EXAMPLE_FILES = {
    key: CONFIG_DIR / f"{key}.example.yaml"
    for key in LOCAL_FILES
}

CUSTOMER_SAFE_SOURCE_TYPES = {
    "knowledge_base",
    "faq",
    "official_help_article",
    "policy",
    "release_note",
    "known_issue",
    "official_internal_doc",
    "reviewed_case_learning",
}

RAW_OR_UNREVIEWED_SOURCE_TYPES = {
    "raw_ticket_history",
    "raw_ticket_chat_call",
    "raw_chat_transcript",
    "raw_call_transcript",
    "unreviewed_case_learning",
    "similar_resolved_ticket",
}

SUGGEST_ONLY_FORBIDDEN_MODES = {
    "auto_send",
    "auto_resolve",
    "account_action",
    "kb_rewrite",
    "autonomous",
}

ALLOWED_OUTPUT_MODES = {
    "resolution_full",
    "email_draft_only",
    "internal_agent_assist",
    "diagnosis_only",
    "custom",
}

DEFAULT_CHUNK_TYPES = [
    "how_to",
    "troubleshooting",
    "faq",
    "concept",
    "warning",
    "table",
    "release_change",
    "known_issue",
    "permission",
    "configuration",
    "policy",
    "billing",
    "integration",
]

SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "knowledge_base": {
        "key": "knowledge_base",
        "display_name": "Knowledge base / FAQ",
        "enabled_default": True,
        "source_type": "official_help_article",
        "default_authority": 1.0,
        "customer_facing_evidence_allowed": True,
        "required_fields": [["title"], ["content", "body"]],
        "recommended_fields": ["url", "product", "platform", "updated_at", "category"],
        "optional_fields": ["source_license", "attribution_required", "attribution_text"],
        "sample_file_path": "knowledge_loader/processed/demo_knowledge_base.csv",
    },
    "policies": {
        "key": "policies",
        "display_name": "Policies",
        "enabled_default": False,
        "source_type": "policy",
        "default_authority": 1.0,
        "customer_facing_evidence_allowed": True,
        "required_fields": [["policy_name", "title"], ["content", "body"]],
        "recommended_fields": ["policy_area", "url", "updated_at", "audience"],
        "optional_fields": ["source_license", "attribution_required", "attribution_text"],
        "sample_file_path": "knowledge_loader/processed/demo_policies.csv",
    },
    "release_notes": {
        "key": "release_notes",
        "display_name": "Release notes / changelog",
        "enabled_default": True,
        "source_type": "release_note",
        "default_authority": 0.9,
        "customer_facing_evidence_allowed": True,
        "required_fields": [["title"], ["content", "body"]],
        "recommended_fields": ["release_date", "version", "platform", "url"],
        "optional_fields": ["source_license", "attribution_required", "attribution_text"],
        "sample_file_path": "knowledge_loader/processed/demo_release_notes.csv",
    },
    "known_issues": {
        "key": "known_issues",
        "display_name": "Known issues",
        "enabled_default": False,
        "source_type": "known_issue",
        "default_authority": 0.85,
        "customer_facing_evidence_allowed": True,
        "required_fields": [["issue_title", "title"], ["symptoms", "content"]],
        "recommended_fields": ["status", "workaround", "affected_platform", "updated_at", "url"],
        "optional_fields": ["source_license", "attribution_required", "attribution_text"],
        "sample_file_path": "knowledge_loader/processed/demo_known_issues.csv",
    },
    "historical_tickets": {
        "key": "historical_tickets",
        "display_name": "Historical tickets",
        "enabled_default": False,
        "source_type": "raw_ticket_history",
        "default_authority": 0.1,
        "customer_facing_evidence_allowed": False,
        "required_fields": [["ticket_id"], ["customer_message", "content"]],
        "recommended_fields": ["agent_resolution", "created_at", "product", "redaction_status"],
        "optional_fields": ["source_license", "attribution_required", "attribution_text"],
        "disabled_reason": "Future/offline-only. Raw support history must be redacted, reviewed, and promoted before use as evidence.",
        "sample_file_path": "knowledge_loader/processed/demo_historical_tickets_offline_only.csv",
    },
}

IMPACT_APPLIES_ON_NEXT_RESOLVE = "Applies on next resolve"
IMPACT_REQUIRES_RELOAD = "Requires knowledge reload"
IMPACT_REQUIRES_RESTART = "Requires app restart"

DEFAULT_CONFIG: dict[str, Any] = {
    "products": {
        "products": {
            "example_product": {
                "display_name": "Example Product",
                "slug": "example_product",
                "aliases": ["demo", "example", "example app"],
                "default_product": True,
                "platforms": {
                    "website": {
                        "normalized": "website",
                        "aliases": ["web", "browser", "site", "reports", "exports", "export"],
                        "enabled": True,
                    },
                    "mobile_app": {
                        "normalized": "app",
                        "aliases": ["mobile", "app", "ios", "android", "push notifications", "badge", "queued reply"],
                        "enabled": True,
                    },
                },
                "roles": {
                    "required": False,
                    "values": [
                        {"name": "admin", "aliases": ["administrator"]},
                        {"name": "compliance_manager", "aliases": ["compliance", "auditor"]},
                        {"name": "account_owner", "aliases": ["owner", "billing owner"]},
                        {"name": "agent", "aliases": ["support agent", "user"]},
                        {"name": "manager", "aliases": ["supervisor", "lead"]},
                    ],
                },
            }
        }
    },
    "sources": {
        "sources": {
            "knowledge_base": {
                "enabled": True,
                "source_type": "official_help_article",
                "path": "knowledge_loader/processed/demo_knowledge_base.csv",
                "audience": "customer_facing",
                "required_columns": ["title", "content"],
                "column_mapping": {
                    "title": "title",
                    "content": "content",
                    "url": "url",
                    "url_name": "url_name",
                },
                "default_authority": 1.0,
            },
            "policies": {
                "enabled": True,
                "source_type": "policy",
                "path": "knowledge_loader/processed/demo_policies.csv",
                "audience": "customer_facing",
                "required_columns": ["policy_name", "content"],
                "column_mapping": {"policy_name": "policy_name", "content": "content", "url": "url"},
                "default_authority": 1.0,
            },
            "release_notes": {
                "enabled": True,
                "source_type": "release_note",
                "path": "knowledge_loader/processed/demo_release_notes.csv",
                "audience": "customer_facing",
                "required_columns": ["title", "content"],
                "column_mapping": {
                    "title": "title",
                    "content": "content",
                    "url": "url",
                    "url_name": "url_name",
                },
                "default_authority": 0.9,
            },
            "known_issues": {
                "enabled": True,
                "source_type": "known_issue",
                "path": "knowledge_loader/processed/demo_known_issues.csv",
                "audience": "customer_facing",
                "required_columns": ["issue_title", "symptoms"],
                "column_mapping": {"issue_title": "issue_title", "symptoms": "symptoms", "url": "url"},
                "default_authority": 0.85,
            },
            "historical_tickets": {
                "enabled": False,
                "source_type": "raw_ticket_history",
                "path": "knowledge_loader/processed/demo_historical_tickets_offline_only.csv",
                "audience": "offline_only",
                "required_columns": ["ticket_id", "customer_message"],
                "column_mapping": {
                    "ticket_id": "ticket_id",
                    "customer_message": "content",
                    "agent_resolution": "agent_resolution",
                },
                "default_authority": 0.1,
            },
        }
    },
    "output": {
        "output": {
            "mode": "resolution_full",
            "audience": "internal_assist",
            "include": {
                "issue_classification": True,
                "diagnosis": True,
                "resolution_steps": True,
                "sources": True,
                "confidence": True,
                "draft_email": True,
                "validation_flags": True,
            },
            "email": {
                "greeting": "Hi,",
                "signoff": "Kind regards,",
                "tone": "professional_warm",
            },
            "diagnosis": {"max_lines": 3},
        }
    },
    "retrieval_policy": {
        "chunk_type_rules": {
            "billing": {
                "enabled": True,
                "priority": 70,
                "heading_keywords": ["billing", "invoice", "payment", "refund", "subscription"],
                "content_keywords": ["invoice", "card", "charge", "payment method", "receipt"],
                "negative_keywords": [],
            },
            "permission": {
                "enabled": True,
                "priority": 65,
                "heading_keywords": ["permission", "role", "access", "admin"],
                "content_keywords": ["permission", "role", "access level", "authorized"],
                "negative_keywords": [],
            },
            "troubleshooting": {
                "enabled": True,
                "priority": 60,
                "heading_keywords": ["troubleshoot", "error", "issue", "problem"],
                "content_keywords": ["not working", "failed", "error", "fix"],
                "negative_keywords": [],
            },
            "how_to": {
                "enabled": True,
                "priority": 55,
                "heading_keywords": ["how to", "steps", "setup", "configure"],
                "content_keywords": ["click", "select", "open", "choose"],
                "negative_keywords": [],
            },
            "integration": {
                "enabled": True,
                "priority": 50,
                "heading_keywords": ["integration", "api", "webhook"],
                "content_keywords": ["api", "webhook", "token", "endpoint"],
                "negative_keywords": [],
            },
            "policy": {
                "enabled": True,
                "priority": 45,
                "heading_keywords": ["policy", "terms", "rules"],
                "content_keywords": ["policy", "required", "allowed", "not allowed"],
                "negative_keywords": [],
            },
            "faq": {
                "enabled": True,
                "priority": 40,
                "heading_keywords": ["faq", "question"],
                "content_keywords": ["frequently asked", "question"],
                "negative_keywords": [],
            },
            "warning": {
                "enabled": True,
                "priority": 35,
                "heading_keywords": ["warning", "important", "note"],
                "content_keywords": ["warning", "important", "note"],
                "negative_keywords": [],
            },
        },
        "route_policies": {
            "bug": {
                "preferred_source_types": ["knowledge_base", "official_help_article", "release_note", "known_issue"],
                "preferred_chunk_types": ["troubleshooting", "known_issue", "release_change", "faq"],
                "boost": 0.15,
                "disallowed_source_types": ["raw_ticket_history", "raw_chat_transcript", "raw_call_transcript"],
            },
            "access": {
                "preferred_source_types": ["knowledge_base", "official_help_article", "policy"],
                "preferred_chunk_types": ["permission", "configuration", "how_to"],
                "boost": 0.12,
                "disallowed_source_types": ["raw_ticket_history"],
            },
            "billing": {
                "preferred_source_types": ["policy", "knowledge_base", "official_help_article"],
                "preferred_chunk_types": ["billing", "policy", "faq", "how_to"],
                "boost": 0.15,
                "disallowed_source_types": ["raw_ticket_history"],
            },
            "how_to": {
                "preferred_source_types": ["knowledge_base", "official_help_article", "faq"],
                "preferred_chunk_types": ["how_to", "configuration", "faq", "concept"],
                "boost": 0.1,
                "disallowed_source_types": ["raw_ticket_history"],
            },
            "integration": {
                "preferred_source_types": ["knowledge_base", "official_help_article", "release_note"],
                "preferred_chunk_types": ["integration", "configuration", "troubleshooting"],
                "boost": 0.12,
                "disallowed_source_types": ["raw_ticket_history"],
            },
            "policy": {
                "preferred_source_types": ["policy", "knowledge_base", "official_help_article"],
                "preferred_chunk_types": ["policy", "faq", "concept"],
                "boost": 0.12,
                "disallowed_source_types": ["raw_ticket_history"],
            },
            "release_change": {
                "preferred_source_types": ["release_note", "known_issue"],
                "preferred_chunk_types": ["release_change", "known_issue", "concept"],
                "boost": 0.12,
                "disallowed_source_types": ["raw_ticket_history"],
            },
            "general": {
                "preferred_source_types": ["knowledge_base", "official_help_article", "faq"],
                "preferred_chunk_types": ["concept", "faq", "how_to"],
                "boost": 0.08,
                "disallowed_source_types": ["raw_ticket_history"],
            },
        },
        "source_authority": {
            "official_help_article": 1.0,
            "knowledge_base": 1.0,
            "policy": 1.0,
            "official_internal_doc": 0.95,
            "release_note": 0.9,
            "known_issue": 0.85,
            "reviewed_case_learning": 0.7,
            "unreviewed_case_learning": 0.45,
            "similar_resolved_ticket": 0.3,
            "raw_ticket_history": 0.1,
            "raw_chat_transcript": 0.1,
            "raw_call_transcript": 0.1,
        },
        "source_authority_presets": {
            "active": "balanced",
            "presets": {
                "strict": {
                    "policy": 1.0,
                    "official_help_article": 0.95,
                    "knowledge_base": 0.95,
                    "faq": 0.9,
                    "release_note": 0.85,
                    "known_issue": 0.75,
                    "reviewed_case_learning": 0.55,
                    "raw_ticket_history": 0.0,
                    "raw_chat_transcript": 0.0,
                    "raw_call_transcript": 0.0,
                    "similar_resolved_ticket": 0.0,
                },
                "balanced": {
                    "policy": 1.0,
                    "official_help_article": 1.0,
                    "knowledge_base": 1.0,
                    "faq": 0.9,
                    "release_note": 0.9,
                    "known_issue": 0.85,
                    "reviewed_case_learning": 0.7,
                    "raw_ticket_history": 0.0,
                    "raw_chat_transcript": 0.0,
                    "raw_call_transcript": 0.0,
                    "similar_resolved_ticket": 0.0,
                },
                "permissive_internal": {
                    "policy": 1.0,
                    "official_help_article": 1.0,
                    "knowledge_base": 1.0,
                    "faq": 0.95,
                    "release_note": 0.9,
                    "known_issue": 0.85,
                    "reviewed_case_learning": 0.8,
                    "raw_ticket_history": 0.0,
                    "raw_chat_transcript": 0.0,
                    "raw_call_transcript": 0.0,
                    "similar_resolved_ticket": 0.0,
                },
            },
        },
            "retrieval": {
                "top_k": 20,
                "reranker_top_k": 5,
                "parent_section_expansion": True,
                "sibling_expansion": True,
                "condition_neighbor_expansion": True,
                "max_expansion_ratio": 2.0,
                "cache_enabled": True,
                "contextual_retrieval": {
                    "enabled": True,
                    "mode": "deterministic",
                    "include_in_embedding_text": True,
                    "include_in_bm25": True,
                    "keep_display_text_clean": True,
                    "ingestion_cost_usd": 0.0,
                },
            },
        "privacy": {
            "pii_redaction": {
                "enabled": True,
                "provider": "presidio",
                "fallback_behavior": "warn_and_skip_sensitive_sources",
                "entities": ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN", "CREDIT_CARD", "ACCOUNT_ID", "ADDRESS"],
            }
        },
    },
    "workflow": {
        "workflow": {
            "mode": "standard",
            "llm_budget_preset": "balanced",
            "max_llm_calls": 2,
            "modes": {
                "fast": {
                    "responder": True,
                    "evaluator": False,
                    "retry_responder_on_low_faithfulness": False,
                },
                "standard": {
                    "responder": True,
                    "evaluator": True,
                    "retry_responder_on_low_faithfulness": False,
                },
                "strict": {
                    "responder": True,
                    "evaluator": True,
                    "retry_responder_on_low_faithfulness": True,
                },
                "custom": {
                    "responder": True,
                    "evaluator": True,
                    "retry_responder_on_low_faithfulness": False,
                },
            },
            "stages": {
                "query_builder": {"enabled": False, "counts_toward_budget": True},
                "responder": {"enabled": True, "counts_toward_budget": True},
                "evaluator": {"enabled": True, "counts_toward_budget": True},
                "responder_retry": {"enabled": False, "counts_toward_budget": True},
            },
            "experiments": {
                "advanced_reasoning": {
                    "enabled": True,
                    "typed_planner_output": True,
                    "multi_query_retrieval": True,
                    "evidence_table": True,
                    "structured_reply": True,
                },
                "retrieval_strategy_v1": {
                    "arm": "current_hybrid_rag",
                    "allowed_arms": [
                        "current_hybrid_rag",
                        "current_rag_query_decomposition",
                        "markdown_canonical_current_rag",
                    ],
                },
            },
            "trace_retention_days": 30,
        }
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        if yaml:
            data = yaml.safe_load(fh) or {}
        else:
            data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a YAML mapping")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        if yaml:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=False)
        else:
            json.dump(data, fh, indent=2)
            fh.write("\n")


def get_source_registry() -> dict[str, dict[str, Any]]:
    return deepcopy(SOURCE_REGISTRY)


def _field_label(group: list[str]) -> str:
    return " or ".join(group)


def _mapped_column(field: str, mapping: dict[str, str]) -> str:
    return str(mapping.get(field) or field)


def _field_available(field: str, columns: set[str], mapping: dict[str, str]) -> bool:
    return _mapped_column(field, mapping) in columns or field in columns


def validate_source_contract(source_key: str, columns: list[str], mapping: dict[str, str] | None = None) -> dict[str, Any]:
    registry = get_source_registry()
    contract = registry.get(source_key)
    if not contract:
        return {
            "source_key": source_key,
            "valid": False,
            "required": [],
            "recommended": [],
            "errors": [f"Unknown source key '{source_key}'."],
            "warnings": [],
        }

    column_set = {str(col) for col in columns}
    mapping = mapping or {}
    required = []
    errors = []
    for group in contract.get("required_fields", []):
        any_of = [str(field) for field in group]
        present = [field for field in any_of if _field_available(field, column_set, mapping)]
        status = {
            "field": _field_label(any_of),
            "any_of": any_of,
            "present": bool(present),
            "mapped_columns": {field: _mapped_column(field, mapping) for field in any_of},
        }
        required.append(status)
        if not status["present"]:
            errors.append(f"Missing required field: {_field_label(any_of)}.")

    recommended = []
    warnings = []
    for field in contract.get("recommended_fields", []):
        present = _field_available(str(field), column_set, mapping)
        recommended.append({
            "field": field,
            "present": present,
            "mapped_column": _mapped_column(str(field), mapping),
        })
        if not present:
            warnings.append(f"Recommended field missing: {field}.")

    if not contract.get("customer_facing_evidence_allowed", True):
        warnings.append(f"{contract['display_name']} is disabled/future-only and cannot be customer-facing evidence.")

    return {
        "source_key": source_key,
        "valid": not errors,
        "required": required,
        "recommended": recommended,
        "errors": errors,
        "warnings": warnings,
    }


def _canonical_value(row: dict[str, Any], canonical: str, mapping: dict[str, str]) -> str:
    source_col = _mapped_column(canonical, mapping)
    value = row.get(source_col, row.get(canonical, ""))
    return "" if value is None else str(value)


def _first_canonical(row: dict[str, Any], fields: list[str], mapping: dict[str, str]) -> str:
    for field in fields:
        value = _canonical_value(row, field, mapping)
        if value.strip():
            return value
    return ""


def build_contextual_retrieval_fields(
    *,
    title: str = "",
    heading_path: str = "",
    product: str = "",
    platform: str = "",
    source_type: str = "",
    role_or_permission: str = "",
    version_or_date: str = "",
    known_issue_status: str = "",
    applies_when: str = "",
    enabled: bool = True,
) -> list[str]:
    if not enabled:
        return []
    return [
        f"Title: {title}" if title else "",
        f"Section: {heading_path}" if heading_path else "",
        f"Product: {product}" if product else "",
        f"Platform: {platform}" if platform else "",
        f"Source type: {source_type}" if source_type else "",
        f"Role or permission: {role_or_permission}" if role_or_permission else "",
        f"Version or date: {version_or_date}" if version_or_date else "",
        f"Known issue status: {known_issue_status}" if known_issue_status else "",
        f"Applies when: {applies_when}" if applies_when else "",
    ]


def preview_source_rows(
    path: str,
    mapping: dict[str, str] | None = None,
    limit: int = 5,
    source_key: str = "knowledge_base",
) -> dict[str, Any]:
    mapping = mapping or {}
    sample_limit = max(1, min(int(limit or 5), 25))
    source_path = Path(path)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    if not source_path.exists():
        return {
            "detected_columns": [],
            "sample_raw_rows": [],
            "sample_canonical_rows": [],
            "sample_chunk_previews": [],
            "validation": validate_source_contract(source_key, [], mapping),
            "warnings": [],
            "errors": [f"Source file not found: {path}."],
            "can_load": False,
        }

    registry = get_source_registry().get(source_key, {})
    try:
        connector = get_connector_for_path(source_path)
        documents, connector_preview = connector.parse(
            source_path,
            source_key=source_key,
            source_type=registry.get("source_type", source_key),
            column_mapping=mapping,
            sample_limit=sample_limit,
        )
    except ConnectorError as exc:
        return {
            "detected_columns": [],
            "sample_raw_rows": [],
            "sample_canonical_rows": [],
            "sample_documents": [],
            "sample_chunk_previews": [],
            "validation": validate_source_contract(source_key, [], mapping),
            "warnings": list(exc.warnings),
            "errors": [str(exc)],
            "can_load": False,
        }

    columns = list(connector_preview.get("detected_columns", []))
    if not columns and documents:
        columns = ["title", "content"]
    rows = list(connector_preview.get("sample_raw_rows", []))

    contract = validate_source_contract(source_key, columns, mapping)
    canonical_rows = []
    sample_documents = []
    chunk_previews = []
    policy = load_config("retrieval_policy")
    contextual_settings = policy.get("retrieval", {}).get("contextual_retrieval", {})
    contextual_enabled = bool(contextual_settings.get("enabled", True))

    for idx, document in enumerate(documents, 1):
        canonical = {
            "title": document.title,
            "content": document.body,
            "url": document.source_url,
            "product": document.product,
            "platform": document.platform,
            "role_or_permission": document.role,
            "version_or_date": document.version_or_date,
            "known_issue_status": str(document.metadata.get("status", "")),
            "applies_when": document.applies_when,
            "source_license": document.source_license,
            "attribution_required": document.attribution_required,
            "attribution_text": document.attribution_text,
            "source_type": document.source_type or registry.get("source_type", source_key),
        }
        canonical_rows.append(canonical)
        sample_documents.append(_document_preview(document))
        text = "\n\n".join(section.text for section in document.sections if section.text).strip()
        if not text:
            text = "\n\n".join(part for part in [canonical["title"], canonical["content"]] if part).strip()
        if text:
            words = text.split()
            heading = canonical["title"]
            context_fields = build_contextual_retrieval_fields(
                title=canonical["title"],
                heading_path=heading,
                product=canonical["product"],
                platform=canonical["platform"],
                source_type=canonical["source_type"],
                role_or_permission=canonical["role_or_permission"],
                version_or_date=canonical["version_or_date"],
                known_issue_status=canonical["known_issue_status"],
                applies_when=canonical["applies_when"],
                enabled=contextual_enabled,
            )
            context = "\n".join(part for part in context_fields if part)
            chunk_text = " ".join(words[:200])
            chunk_previews.append({
                "row_number": idx,
                "source_row": idx,
                "source_type": canonical["source_type"],
                "chunk_index": 0,
                "chunk_type": "preview",
                "heading_path": heading,
                "parent_section": heading,
                "applies_when_flags": [flag for flag in [
                    "role_or_permission" if canonical["role_or_permission"] else "",
                    "version_or_date" if canonical["version_or_date"] else "",
                    "known_issue_status" if canonical["known_issue_status"] else "",
                    "applies_when" if canonical["applies_when"] else "",
                ] if flag],
                "source_tier": registry.get("default_authority", ""),
                "approval_state": "approved" if registry.get("customer_facing_evidence_allowed", True) else "not_customer_facing",
                "warnings": [],
                "attribution": {
                    "source_license": canonical["source_license"],
                    "attribution_required": bool(canonical["attribution_required"]),
                    "attribution_text": canonical["attribution_text"],
                },
                "contextual_retrieval": {
                    "enabled": contextual_enabled,
                    "mode": contextual_settings.get("mode", "deterministic"),
                    "ingestion_cost_usd": float(contextual_settings.get("ingestion_cost_usd", 0.0) or 0.0),
                },
                "embedding_text": "\n\n".join(part for part in [context, chunk_text] if part),
                "display_text": "\n\n".join(part for part in [
                    f"Article: {canonical['title']}" if canonical["title"] else "",
                    chunk_text,
                ] if part),
                "word_count": min(len(words), 200),
            })

    errors = list(contract["errors"])
    warnings = list(contract["warnings"]) + list(connector_preview.get("warnings", []))
    for row in canonical_rows:
        if bool(row.get("attribution_required")):
            warnings.append("Attribution is required for at least one previewed row; include attribution in exports.")
    return {
        "detected_columns": columns,
        "sample_raw_rows": rows,
        "sample_canonical_rows": canonical_rows,
        "sample_documents": sample_documents,
        "sample_chunk_previews": chunk_previews,
        "validation": contract,
        "warnings": warnings,
        "errors": errors,
        "can_load": not errors and registry.get("customer_facing_evidence_allowed", True),
    }


def _document_preview(document: SourceDocument) -> dict[str, Any]:
    return {
        "source_key": document.source_key,
        "source_type": document.source_type,
        "source_path": document.source_path,
        "source_url": document.source_url,
        "title": document.title,
        "body_preview": document.body[:500],
        "section_count": len(document.sections),
        "sections": [
            {
                "section_id": section.section_id,
                "heading_path": section.heading_path,
                "page_or_sheet_ref": section.page_or_sheet_ref,
                "row_ref": section.row_ref,
                "text_preview": section.text[:300],
            }
            for section in document.sections[:10]
        ],
        "metadata": document.metadata,
    }


def preview_source(
    source_key: str,
    path: str,
    source_type: str = "",
    column_mapping: dict[str, str] | None = None,
    sample_row_limit: int = 5,
) -> dict[str, Any]:
    registry = get_source_registry()
    contract = registry.get(source_key, {})
    result = preview_source_rows(path, column_mapping or {}, sample_row_limit, source_key)
    warnings = list(result.get("warnings", []))
    errors = list(result.get("errors", []))
    if source_key == "historical_tickets" or not contract.get("customer_facing_evidence_allowed", True):
        errors.append("Historical/raw support data is future/offline-only and cannot be loaded as customer-facing evidence.")
    return {
        "source_key": source_key,
        "source_type": source_type or contract.get("source_type", source_key),
        "source_contract": contract,
        **result,
        "warnings": warnings,
        "errors": errors,
        "can_load": result.get("can_load", False) and not errors,
    }


def classify_config_impact(setting_path: str) -> str:
    path = str(setting_path or "").strip().lower()
    restart_markers = (
        "database_url", "knowledge_schema", "ops_schema", "active_provider", "model", "warmup",
        "server", "server.auth", "api_key", "environment",
    )
    reload_markers = (
        "sources.", "source.path", "source.enabled", "column_mapping", "required_columns",
        "chunk_type_rules", "parent_section_expansion", "contextual_retrieval", "source_type", "condition_flags",
        "condition flag",
    )
    live_markers = (
        "output.", "workflow.", "route_policies", "source_authority", "audience",
        "mode", "include", "evaluator", "retry",
    )
    if any(marker in path for marker in restart_markers):
        return IMPACT_REQUIRES_RESTART
    if any(marker in path for marker in reload_markers):
        return IMPACT_REQUIRES_RELOAD
    if any(marker in path for marker in live_markers):
        return IMPACT_APPLIES_ON_NEXT_RESOLVE
    return IMPACT_APPLIES_ON_NEXT_RESOLVE


def config_impact_labels() -> dict[str, str]:
    paths = [
        "output.mode",
        "output.include",
        "output.audience",
        "workflow.mode",
        "workflow.evaluator",
        "workflow.retry",
        "retrieval_policy.route_policies",
        "retrieval_policy.source_authority",
        "sources.path",
        "sources.enabled",
        "sources.column_mapping",
        "sources.source_type",
        "retrieval_policy.chunk_type_rules",
        "retrieval_policy.retrieval.parent_section_expansion",
        "retrieval_policy.condition_flags",
        "DATABASE_URL",
        "KNOWLEDGE_SCHEMA",
        "OPS_SCHEMA",
        "ACTIVE_PROVIDER",
        "MODELS",
        "WARM_LOCAL_MODELS",
        "server.auth.environment",
    ]
    return {path: classify_config_impact(path) for path in paths}


def config_field_metadata() -> dict[str, dict[str, Any]]:
    fields = {
        "product.display_name": ("Identifies the product in UI and request context.", False),
        "product.slug": ("Stable product key used in request context.", True),
        "product.aliases": ("Helps match incoming tickets to the configured product.", False),
        "product.platforms": ("Controls platform labels and retrieval filters.", False),
        "product.roles": ("Controls role context sent to planner and validator.", False),
        "output.mode": ("Controls which response sections agents see.", False),
        "output.audience": ("Controls whether output is framed as internal assist or customer draft.", False),
        "output.include": ("Controls visible response sections.", False),
        "sources.enabled": ("Enables or disables a source category for loading.", True),
        "sources.path": ("Points the loader at a CSV source file.", True),
        "sources.source_type": ("Sets source safety and authority behavior.", True),
        "sources.column_mapping": ("Maps source columns to canonical fields.", True),
        "retrieval_policy.chunk_type_rules": ("Changes deterministic chunk classification.", True),
        "retrieval_policy.route_policies": ("Changes route-aware retrieval preferences.", False),
        "retrieval_policy.source_authority": ("Changes ranking weights while forbidden source clamps still apply.", True),
        "retrieval_policy.retrieval.parent_section_expansion": ("Changes retrieval context expansion behavior.", False),
        "retrieval_policy.privacy.pii_redaction": ("Controls redaction during loading and output safety checks.", True),
        "workflow.mode": ("Controls evaluator/retry behavior within suggest-only mode.", False),
        "workflow.max_llm_calls": ("Caps default hot-path LLM calls.", True),
    }
    result = {}
    for path, (reason, requires_confirmation) in fields.items():
        result[path] = {
            "impact": classify_config_impact(path),
            "reason": reason,
            "apply_action": _apply_action_for_impact(classify_config_impact(path)),
            "requires_confirmation": requires_confirmation,
        }
    return result


def _apply_action_for_impact(impact: str) -> str:
    if impact == IMPACT_REQUIRES_RESTART:
        return "restart_app"
    if impact == IMPACT_REQUIRES_RELOAD:
        return "reload_knowledge"
    return "next_resolve"


def setup_wizard_status(config_data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = config_data or load_config()
    products = data.get("products", {}).get("products", {})
    sources = data.get("sources", {}).get("sources", {})
    enabled_sources = [name for name, source in sources.items() if source.get("enabled")]
    validation = validate_config(data)
    demo_paths = [
        source.get("path", "")
        for source in sources.values()
        if str(source.get("path", "")).startswith("knowledge_loader/processed/demo_")
    ]
    steps = {
        "detect_first_run": True,
        "choose_demo_or_custom_source": bool(enabled_sources),
        "validate_source_contracts": validation["valid"],
        "map_columns": all(bool((sources[name].get("column_mapping") or {})) for name in enabled_sources),
        "preview_rows": False,
        "preview_chunks": False,
        "run_three_sample_tickets": False,
        "save_config": bool(products and enabled_sources),
        "show_next_action": True,
        "show_reload_restart_notices": True,
    }
    completed = sum(1 for value in steps.values() if value)
    return {
        "first_run": not bool(products and enabled_sources),
        "source_mode": "demo" if demo_paths else "custom",
        "steps": steps,
        "completion_ratio": round(completed / len(steps), 4),
        "target_completion_ratio": 0.8,
        "move_wizard_fixes_first": completed / len(steps) < 0.5,
        "next_action": (
            "Preview one enabled source, then run three sample tickets."
            if validation["valid"] else "Fix config validation errors before loading knowledge."
        ),
    }


def load_config(section: str | None = None, include_examples: bool = True) -> dict[str, Any]:
    sections = [section] if section else list(DEFAULT_CONFIG.keys())
    result: dict[str, Any] = {}
    for key in sections:
        if key not in DEFAULT_CONFIG:
            raise ValueError(f"Unknown config section: {key}")
        base = deepcopy(DEFAULT_CONFIG[key])
        example = _read_yaml(EXAMPLE_FILES[key]) if include_examples else {}
        local = _read_yaml(LOCAL_FILES[key])
        result[key] = _deep_merge(_deep_merge(base, example), local)
    return result[section] if section else result


def resolved_config_files() -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for key in DEFAULT_CONFIG:
        local_path = LOCAL_FILES[key].resolve()
        example_path = EXAMPLE_FILES[key].resolve()
        if local_path.exists():
            source = "local"
            active_path = local_path
        elif example_path.exists():
            source = "example"
            active_path = example_path
        else:
            source = "default"
            active_path = local_path
        files[key] = {
            "source": source,
            "active_path": str(active_path),
            "local_path": str(local_path),
            "local_exists": local_path.exists(),
            "example_path": str(example_path),
            "example_exists": example_path.exists(),
        }
    return files


def validate_runtime_config_files() -> dict[str, Any]:
    results: dict[str, Any] = {}
    errors: list[str] = []
    for key in DEFAULT_CONFIG:
        path = LOCAL_FILES[key] if LOCAL_FILES[key].exists() else EXAMPLE_FILES[key]
        label = str(path.resolve())
        try:
            data = _read_yaml(path)
            merged = load_config(key)
            validation = validate_config({**load_config(), key: merged})
            status = "ok" if validation["valid"] else "fail"
            section_errors = [
                f"{label} -> {key} -> validation -> {message}"
                for message in validation.get("errors", [])
            ]
        except Exception as exc:
            status = "fail"
            section_errors = [f"{label} -> {key} -> parse -> {exc}"]
        results[key] = {
            "status": status,
            "path": label,
            "errors": section_errors,
        }
        errors.extend(section_errors)
    return {"valid": not errors, "files": results, "errors": errors}


def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Config payload must be an object")

    merged = load_config()
    for key, value in payload.items():
        if key in merged:
            if not isinstance(value, dict):
                raise ValueError(f"{key} config must be an object")
            merged[key] = value
    validation = validate_config(merged)
    if not validation["valid"]:
        raise ValueError("Invalid config: " + "; ".join(validation["errors"]))

    saved = []
    for key, path in LOCAL_FILES.items():
        if key in payload:
            if not isinstance(payload[key], dict):
                raise ValueError(f"{key} config must be an object")
            _write_yaml(path, payload[key])
            saved.append(str(path.relative_to(PROJECT_ROOT)))

    if not saved:
        raise ValueError("No known config sections supplied")

    config_data = load_config()
    return {
        "saved": saved,
        "config": config_data,
        "apply_status": describe_apply_status(saved),
        "impact_labels": config_impact_labels(),
        "config_version": config_fingerprint(config_data),
    }


def describe_apply_status(saved: list[str]) -> str:
    joined = " ".join(saved)
    loader_files = ("sources.yaml", "retrieval_policy.yaml")
    if any(name in joined for name in ("products.yaml", "output.yaml", "workflow.yaml")):
        base = "Saved. Live-safe response settings apply on the next /resolve."
    else:
        base = "Saved."
    if any(name in joined for name in loader_files):
        return base + " Reload knowledge base to apply source paths, column mappings, chunk metadata, parent-section behavior, or source type changes to indexed rows."
    return base


def write_example_configs() -> list[str]:
    written = []
    for key, path in EXAMPLE_FILES.items():
        if not path.exists():
            _write_yaml(path, DEFAULT_CONFIG[key])
            written.append(str(path.relative_to(PROJECT_ROOT)))
    return written


def get_default_product(config_data: dict[str, Any] | None = None) -> dict[str, Any]:
    products = (config_data or load_config("products")).get("products", {})
    for slug, product in products.items():
        if product.get("default_product"):
            return {"slug": slug, **product}
    if products:
        slug, product = next(iter(products.items()))
        return {"slug": slug, **product}
    return {"slug": "", "display_name": ""}


def normalize_product_for_retrieval(product_value: str = "", config_data: dict[str, Any] | None = None) -> str:
    products = (config_data or load_config("products")).get("products", {})
    requested = str(product_value or "").strip().lower()
    default_product = get_default_product({"products": products})

    for slug, product in products.items():
        aliases = [
            slug,
            product.get("slug", ""),
            product.get("display_name", ""),
            *(product.get("aliases") or []),
        ]
        if requested and requested in {str(alias).strip().lower() for alias in aliases if alias}:
            return str(product.get("display_name") or product.get("slug") or slug).strip().lower()

    if requested:
        return requested
    return str(default_product.get("display_name") or default_product.get("slug") or "").strip().lower()


def product_values_for_retrieval(product_value: str = "", config_data: dict[str, Any] | None = None) -> list[str]:
    products = (config_data or load_config("products")).get("products", {})
    requested = str(product_value or "").strip().lower()
    normalized = normalize_product_for_retrieval(product_value, {"products": products})
    values = {normalized}

    for slug, product in products.items():
        aliases = [
            slug,
            str(slug).replace("_", " "),
            product.get("slug", ""),
            str(product.get("slug", "")).replace("_", " "),
            product.get("display_name", ""),
            *(product.get("aliases") or []),
        ]
        lower_aliases = {str(alias).strip().lower() for alias in aliases if alias}
        if not requested or requested in lower_aliases or normalized in lower_aliases:
            values.update(lower_aliases)

    return sorted(value for value in values if value)


def canonical_product_for_ingestion(product_value: str = "", config_data: dict[str, Any] | None = None) -> str:
    products = (config_data or load_config("products")).get("products", {})
    requested = str(product_value or "").strip().lower()
    default_product = get_default_product({"products": products})

    for slug, product in products.items():
        aliases = [
            slug,
            str(slug).replace("_", " "),
            product.get("slug", ""),
            str(product.get("slug", "")).replace("_", " "),
            product.get("display_name", ""),
            *(product.get("aliases") or []),
        ]
        if requested and requested in {str(alias).strip().lower() for alias in aliases if alias}:
            return str(product.get("display_name") or product.get("slug") or slug).strip()

    if requested:
        return str(product_value).strip()
    return str(default_product.get("display_name") or default_product.get("slug") or "").strip()


def _enabled_platforms_for_product(product_value: str = "", config_data: dict[str, Any] | None = None) -> dict[str, Any]:
    products = (config_data or load_config("products")).get("products", {})
    requested = str(product_value or "").strip().lower()
    default_product = get_default_product({"products": products})

    for slug, product in products.items():
        aliases = [
            slug,
            product.get("slug", ""),
            product.get("display_name", ""),
            *(product.get("aliases") or []),
        ]
        if requested and requested in {str(alias).strip().lower() for alias in aliases if alias}:
            return {
                key: value for key, value in (product.get("platforms") or {}).items()
                if value.get("enabled", True)
            }
    return {
        key: value for key, value in (default_product.get("platforms") or {}).items()
        if value.get("enabled", True)
    }


def normalize_platform_for_retrieval(
    access_channel: str = "",
    product_value: str = "",
    config_data: dict[str, Any] | None = None,
) -> str:
    platforms = _enabled_platforms_for_product(product_value, config_data)
    requested = str(access_channel or "").strip().lower()
    for key, platform in platforms.items():
        aliases = [
            key,
            platform.get("normalized", ""),
            *(platform.get("aliases") or []),
        ]
        if requested and requested in {str(alias).strip().lower() for alias in aliases if alias}:
            return str(platform.get("normalized") or key).strip().lower()
    if requested:
        return requested
    if platforms:
        key, platform = next(iter(platforms.items()))
        return str(platform.get("normalized") or key).strip().lower()
    return "website"


def platform_label(access_channel: str = "", product_value: str = "", config_data: dict[str, Any] | None = None) -> str:
    platforms = _enabled_platforms_for_product(product_value, config_data)
    requested = str(access_channel or "").strip().lower()
    for key, platform in platforms.items():
        aliases = [
            key,
            platform.get("normalized", ""),
            *(platform.get("aliases") or []),
        ]
        if requested and requested in {str(alias).strip().lower() for alias in aliases if alias}:
            return str(platform.get("label") or platform.get("normalized") or key).replace("_", " ")
    return normalize_platform_for_retrieval(access_channel, product_value, config_data).replace("_", " ")


def get_source_category(filename: str, source_config: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    sources = (source_config or load_config("sources")).get("sources", {})
    normalized = filename.lower()
    for name, settings in sources.items():
        path = str(settings.get("path", "")).lower()
        if path and Path(path).name.lower() == normalized:
            return name, settings
    if "release_note" in normalized or "release-notes" in normalized:
        return "release_notes", sources.get("release_notes", {})
    return "knowledge_base", sources.get("knowledge_base", {})


def source_authority_weights(policy_config: dict[str, Any] | None = None) -> dict[str, float]:
    policy = policy_config or load_config("retrieval_policy")
    authority = dict(policy.get("source_authority", {}) or {})
    preset_config = policy.get("source_authority_presets", {}) or {}
    active = str(preset_config.get("active") or "").strip()
    presets = preset_config.get("presets", {}) or {}
    if active and active in presets:
        authority.update(presets[active] or {})

    for source_type in RAW_OR_UNREVIEWED_SOURCE_TYPES:
        if source_type in authority:
            authority[source_type] = min(float(authority.get(source_type) or 0.0), 0.0)
    return {key: float(value) for key, value in authority.items()}


def get_source_authority(source_type: str, policy_config: dict[str, Any] | None = None) -> float:
    authority = source_authority_weights(policy_config)
    try:
        return float(authority.get(source_type, authority.get("knowledge_base", 1.0)))
    except (TypeError, ValueError):
        return 1.0


def validate_config(config_data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = config_data or load_config()
    errors: list[str] = []
    warnings: list[str] = []

    products = data.get("products", {}).get("products", {})
    if not products:
        errors.append("At least one product must be configured.")
    for slug, product in products.items():
        if not re.match(r"^[a-z0-9_][a-z0-9_-]*$", slug):
            errors.append(f"Product slug '{slug}' should use lowercase letters, numbers, dash, or underscore.")
        if not product.get("display_name"):
            errors.append(f"Product '{slug}' needs a display_name.")

    sources = data.get("sources", {}).get("sources", {})
    enabled_sources = [name for name, settings in sources.items() if settings.get("enabled")]
    if not enabled_sources:
        errors.append("Enable at least one source category.")
    for name, settings in sources.items():
        source_type = settings.get("source_type", name)
        if settings.get("enabled") and not settings.get("path"):
            errors.append(f"Enabled source '{name}' needs a path.")
        if source_type in RAW_OR_UNREVIEWED_SOURCE_TYPES and settings.get("audience") == "customer_facing":
            errors.append(f"Source '{name}' is raw/unreviewed and cannot be customer-facing evidence.")
        if name == "historical_tickets" and settings.get("audience") == "customer_facing":
            errors.append("Historical tickets are future/offline-only and cannot be customer-facing evidence.")
        registry = SOURCE_REGISTRY.get(name)
        if registry and not registry.get("customer_facing_evidence_allowed", True) and settings.get("enabled"):
            errors.append(f"Source '{name}' is future/offline-only and cannot be enabled for customer-facing loading.")
        if name in {"historical_tickets", "chats", "emails", "call_transcripts"} and settings.get("enabled"):
            pii = data.get("retrieval_policy", {}).get("privacy", {}).get("pii_redaction", {})
            if pii.get("enabled") and pii.get("provider") == "presidio":
                try:
                    __import__("presidio_analyzer")
                except Exception:
                    warnings.append(
                        f"Source '{name}' is sensitive. Presidio is configured but unavailable; loading should skip or block it."
                    )

    authority = data.get("retrieval_policy", {}).get("source_authority", {})
    preset_config = data.get("retrieval_policy", {}).get("source_authority_presets", {}) or {}
    active_preset = str(preset_config.get("active") or "").strip()
    presets = preset_config.get("presets", {}) or {}
    if active_preset and active_preset not in presets:
        errors.append(f"Unknown source authority preset '{active_preset}'.")
    top_authority_count = 0
    for source_type, value in authority.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            errors.append(f"Authority for '{source_type}' must be numeric.")
            continue
        if numeric < 0 or numeric > 1:
            errors.append(f"Authority for '{source_type}' must be between 0.0 and 1.0.")
        if numeric == 1.0:
            top_authority_count += 1
        if source_type in RAW_OR_UNREVIEWED_SOURCE_TYPES and numeric >= 1.0:
            errors.append(f"Raw/unreviewed source '{source_type}' cannot have authority 1.0.")
    for preset_name, weights in presets.items():
        if preset_name not in {"strict", "balanced", "permissive_internal"}:
            warnings.append(f"Unknown source authority preset '{preset_name}' will be treated as advanced configuration.")
        for raw_type in RAW_OR_UNREVIEWED_SOURCE_TYPES:
            if float((weights or {}).get(raw_type, 0.0) or 0.0) > 0:
                errors.append(f"Preset '{preset_name}' cannot make forbidden source '{raw_type}' customer-facing.")
    if top_authority_count > 3:
        warnings.append("More than three source types have authority 1.0; ranking may become less useful.")

    known_chunk_types = set(DEFAULT_CHUNK_TYPES)
    route_policies = data.get("retrieval_policy", {}).get("route_policies", {})
    for route, policy in route_policies.items():
        unknown = [ct for ct in policy.get("preferred_chunk_types", []) if ct not in known_chunk_types]
        if unknown:
            warnings.append(f"Route '{route}' references custom chunk types: {', '.join(unknown)}.")

    workflow = data.get("workflow", {}).get("workflow", {})
    mode = str(workflow.get("mode") or "").strip().lower()
    if mode in workflow.get("modes", {}):
        selected = workflow.get("modes", {}).get(mode, {})
        stages_for_validation = deepcopy(workflow.get("stages", {}))
        stages_for_validation.setdefault("responder", {})
        stages_for_validation.setdefault("evaluator", {})
        stages_for_validation.setdefault("responder_retry", {})
        stages_for_validation["responder"]["enabled"] = bool(selected.get("responder", True))
        stages_for_validation["evaluator"]["enabled"] = bool(selected.get("evaluator", True))
        stages_for_validation["responder_retry"]["enabled"] = bool(selected.get("retry_responder_on_low_faithfulness", False))
        workflow = {**workflow, "stages": stages_for_validation}
    max_calls = int(workflow.get("max_llm_calls", 2) or 0)
    if mode == "fast":
        max_calls = min(max_calls or 1, 1)
    elif mode == "standard":
        max_calls = max(max_calls, 2)
    elif mode == "strict":
        max_calls = max(max_calls, 3)
    enabled_counted = [
        name for name, stage in workflow.get("stages", {}).items()
        if stage.get("enabled") and stage.get("counts_toward_budget")
    ]
    if len(enabled_counted) > max_calls:
        errors.append("Enabled LLM stages exceed max_llm_calls.")

    output = data.get("output", {}).get("output", {})
    output_mode = str(output.get("mode") or "").strip().lower()
    if output_mode in SUGGEST_ONLY_FORBIDDEN_MODES:
        errors.append(f"Output mode '{output_mode}' is not supported. v3.x is suggest-only.")
    elif output_mode and output_mode not in ALLOWED_OUTPUT_MODES:
        errors.append(f"Unknown output mode '{output_mode}'.")

    try:
        retention_days = int(data.get("workflow", {}).get("workflow", {}).get("trace_retention_days", 30) or 30)
        if retention_days < 1:
            errors.append("workflow.trace_retention_days must be at least 1.")
    except (TypeError, ValueError):
        errors.append("workflow.trace_retention_days must be an integer.")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def output_preferences() -> dict[str, Any]:
    return load_config("output").get("output", {})


def config_fingerprint(config_data: dict[str, Any] | None = None, sections: list[str] | None = None) -> str:
    data = config_data or load_config()
    selected = {key: data.get(key, {}) for key in (sections or sorted(data.keys()))}
    payload = json.dumps(selected, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def runtime_fingerprint() -> str:
    return config_fingerprint(load_config(), ["products", "output", "workflow", "retrieval_policy"])


def response_fingerprint() -> str:
    return config_fingerprint(load_config(), ["products", "output", "workflow"])


def retrieval_fingerprint() -> str:
    return config_fingerprint(load_config(), ["products", "retrieval_policy"])


def workflow_settings() -> dict[str, Any]:
    workflow = deepcopy(load_config("workflow").get("workflow", {}))
    mode = str(workflow.get("mode") or "").strip().lower()
    modes = workflow.get("modes", {})
    if mode and mode in modes:
        selected = modes.get(mode, {})
        stages = deepcopy(workflow.get("stages", {}))
        stages.setdefault("responder", {"counts_toward_budget": True})
        stages.setdefault("evaluator", {"counts_toward_budget": True})
        stages.setdefault("responder_retry", {"counts_toward_budget": True})
        stages["responder"]["enabled"] = bool(selected.get("responder", True))
        stages["evaluator"]["enabled"] = bool(selected.get("evaluator", True))
        stages["responder_retry"]["enabled"] = bool(selected.get("retry_responder_on_low_faithfulness", False))
        workflow["stages"] = stages
        if mode == "fast":
            workflow["llm_budget_preset"] = "minimal"
            workflow["max_llm_calls"] = min(int(workflow.get("max_llm_calls", 1) or 1), 1)
        elif mode == "strict":
            workflow["llm_budget_preset"] = "strict_quality"
            workflow["max_llm_calls"] = max(int(workflow.get("max_llm_calls", 3) or 3), 3)
        else:
            workflow["llm_budget_preset"] = "balanced"
            workflow["max_llm_calls"] = max(int(workflow.get("max_llm_calls", 2) or 2), 2)
    return workflow


def experiment_settings(name: str) -> dict[str, Any]:
    workflow = workflow_settings()
    experiments = workflow.get("experiments", {})
    value = experiments.get(name, {})
    return value if isinstance(value, dict) else {}
