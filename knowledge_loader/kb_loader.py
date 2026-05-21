"""
kb_loader.py — Knowledge Base CSV to Vector DB Loader

Reads scraped CSV files from knowledge_loader/processed/
Chunks, embeds, and loads into:
  - knowledge.knowledge_base            — id, embedding, product, platform, doc_type
  - knowledge.knowledge_base_identifier — id, title, url_name, url, content, chunk metadata
  - knowledge.article_section           — parent section metadata for retrieval expansion

Product and platform are auto-extracted from column B (title):
  KB articles:    "Product Name | Article Title"
  Release notes:  "Product Name | WebApp | 04.07.26"

Usage:
    python knowledge_loader/kb_loader.py
"""
import os
import sys
import re
import time
import json
import hashlib
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT     = os.path.dirname(BASE_DIR)
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import psycopg2
import pandas as pd
from sentence_transformers import SentenceTransformer
from backend.core import config
from backend.core import project_config
from backend.core.run_trace import redact_text, redaction_status
from backend.db.schema import (
    _safe_schema_name,
    ensure_vector_schema,
    ensure_ops_schema,
    INSERT_CHUNK,
    INSERT_KB_IDENTIFIER,
    INSERT_ARTICLE_SECTION,
)

PROCESSED_DIR = os.path.join(BASE_DIR, "processed")

_model = None
LAST_IMPORT_SUMMARIES: list[dict] = []
LOADER_VERSION = "v3.1-evidence-metadata"


def get_embedding(text: str) -> list:
    global _model
    if _model is None:
        print("Loading embedding model...", flush=True)
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        print("Model ready.\n", flush=True)
    return _model.encode(text).tolist()


# ── DB helpers ─────────────────────────────────────────────────
def get_vec_connection():
    url = config.DATABASE_URL
    if not url:
        url = input("  DATABASE_URL not found in .env — paste it here:\n  > ").strip()
    conn = psycopg2.connect(url)
    schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}", public;')
    return conn


def get_ops_connection():
    url = config.DATABASE_URL
    if not url:
        url = input("  DATABASE_URL not found in .env — paste it here:\n  > ").strip()
    conn = psycopg2.connect(url)
    schema = _safe_schema_name(config.OPS_SCHEMA)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}", public;')
    return conn


def get_loaded_ids(vec_conn) -> set:
    with vec_conn.cursor() as cur:
        cur.execute("SELECT id FROM knowledge_base;")
        return {row[0] for row in cur.fetchall()}


def get_loaded_document_hashes(vec_conn) -> dict[str, str]:
    with vec_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(article_id, ''), MAX(COALESCE(document_hash, ''))
            FROM knowledge_base_identifier
            WHERE COALESCE(article_id, '') <> ''
              AND COALESCE(is_active, TRUE) = TRUE
            GROUP BY article_id
            """
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def get_loaded_document_versions(vec_conn) -> dict[str, int]:
    with vec_conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(article_id, ''), MAX(COALESCE(document_version, 1))
            FROM knowledge_base_identifier
            WHERE COALESCE(article_id, '') <> ''
            GROUP BY article_id
            """
        )
        return {row[0]: int(row[1] or 1) for row in cur.fetchall()}


def next_document_version(article_id: str, document_hash: str, existing_hashes: dict[str, str], existing_versions: dict[str, int]) -> int:
    existing_hash = existing_hashes.get(article_id, "")
    existing_version = int(existing_versions.get(article_id, 1) or 1)
    if existing_hash and existing_hash != document_hash:
        return existing_version + 1
    return existing_version


def plan_document_reingestion(
    *,
    article_id: str,
    document_hash: str,
    desired_chunk_ids: list[str],
    existing_document_hashes: dict[str, str],
    loaded_ids: set[str],
) -> dict:
    existing_hash = existing_document_hashes.get(article_id, "")
    existing_chunk_ids = {chunk_id for chunk_id in loaded_ids if chunk_id.startswith(f"{article_id}_")}
    desired = set(desired_chunk_ids)
    if not existing_hash:
        action = "insert"
    elif existing_hash == document_hash and desired <= loaded_ids:
        action = "skip_unchanged"
    else:
        action = "upsert_changed"
    return {
        "article_id": article_id,
        "action": action,
        "existing_document_hash": existing_hash,
        "new_document_hash": document_hash,
        "chunks_to_upsert": sorted(desired if action != "skip_unchanged" else set()),
        "chunks_to_delete": sorted(existing_chunk_ids - desired),
    }


def tombstone_existing_document_chunks(cur, *, article_id: str, superseded_by_chunk_ids: list[str], reason: str = "document_reingested") -> None:
    superseded_by = ",".join(superseded_by_chunk_ids)
    cur.execute(
        """
        UPDATE knowledge_base_identifier
        SET is_active = FALSE,
            active_until = NOW()::TEXT,
            superseded_at = NOW()::TEXT,
            superseded_reason = %s,
            superseded_by_chunk_id = %s
        WHERE article_id = %s
          AND COALESCE(is_active, TRUE) = TRUE
        """,
        (reason, superseded_by, article_id),
    )


# ── CSV reading ───────────────────────────────────────────────
KEEP_COLS = ["id", "title", "url_name", "url", "content"]
OPTIONAL_COLS = [
    "platform",
    "affected_platform",
    "role",
    "permission",
    "version",
    "release_date",
    "status",
    "workaround",
    "applies_when",
    "source_license",
    "attribution_required",
    "attribution_text",
]


def empty_import_summary(source_key: str, source_path: str) -> dict:
    return {
        "source_key": source_key,
        "source_path": source_path,
        "total_rows_seen": 0,
        "rows_loaded": 0,
        "rows_skipped": 0,
        "rows_failed": 0,
        "skipped_row_reasons": [],
        "failed_row_reasons": [],
        "chunks_created": 0,
        "parent_sections_created": 0,
        "warnings": [],
    }


def get_last_import_summaries() -> list[dict]:
    return list(LAST_IMPORT_SUMMARIES)


def is_demo_source_path(path: str) -> bool:
    return os.path.basename(str(path)).startswith("demo_")


def configured_source_paths() -> list[str]:
    sources = project_config.load_config("sources").get("sources", {})
    paths = []
    for settings in sources.values():
        if not settings.get("enabled", False):
            continue
        path = str(settings.get("path") or "").strip()
        if path:
            paths.append(path)
    return paths


def loader_source_paths() -> list[str]:
    if config.DEMO_MODE:
        return [
            os.path.join(PROCESSED_DIR, filename)
            for filename in sorted(os.listdir(PROCESSED_DIR))
            if filename.endswith(".csv")
        ]

    custom_paths = [
        path for path in configured_source_paths()
        if path.endswith(".csv") and not is_demo_source_path(path)
    ]
    if custom_paths:
        return custom_paths

    return [
        os.path.join(PROCESSED_DIR, filename)
        for filename in sorted(os.listdir(PROCESSED_DIR))
        if filename.endswith(".csv") and not is_demo_source_path(filename)
    ]


def read_csv(path: str, source_settings: dict | None = None) -> pd.DataFrame:
    source_settings = source_settings or {}
    mapping = source_settings.get("column_mapping") or {}
    raw = pd.read_csv(path, engine="python")
    df = pd.DataFrame()

    for canonical in KEEP_COLS:
        source_col = mapping.get(canonical, canonical)
        if source_col in raw.columns:
            df[canonical] = raw[source_col]
        elif canonical == "title":
            fallback = next((col for col in ["policy_name", "issue_title"] if col in raw.columns), None)
            if fallback:
                df[canonical] = raw[fallback]
            else:
                raise ValueError(f"Missing expected column '{source_col}' for '{canonical}' in {os.path.basename(path)}")
        elif canonical == "content":
            fallback = next((col for col in ["body", "symptoms", "customer_message"] if col in raw.columns), None)
            if fallback:
                df[canonical] = raw[fallback]
            else:
                raise ValueError(f"Missing expected column '{source_col}' for '{canonical}' in {os.path.basename(path)}")
        elif canonical in {"id", "url_name", "url"}:
            df[canonical] = ""
        else:
            raise ValueError(f"Missing expected column '{source_col}' for '{canonical}' in {os.path.basename(path)}")

    for canonical in OPTIONAL_COLS:
        source_col = mapping.get(canonical, canonical)
        if source_col in raw.columns:
            df[canonical] = raw[source_col]
        else:
            df[canonical] = ""

    for canonical in source_settings.get("required_columns", []):
        source_col = mapping.get(canonical, canonical)
        if source_col not in raw.columns and canonical not in df.columns:
            raise ValueError(f"Missing required column '{source_col}' in {os.path.basename(path)}")

    df = df[KEEP_COLS + OPTIONAL_COLS]
    df = df[df["content"].notna() & (df["content"].str.strip() != "")]
    return df.reset_index(drop=True)


def normalize_rows(df: pd.DataFrame) -> list[dict]:
    return [dict(row) for _, row in df.reset_index(drop=True).iterrows()]


def chunk_rows(rows: list[dict], *, id_prefix: str, doc_type: str, policy_config: dict | None = None) -> list[dict]:
    policy_config = policy_config or project_config.load_config("retrieval_policy")
    chunked = []
    for row in rows:
        raw_title = str(row.get("title") or "").strip()
        url_name = str(row.get("url_name") or row.get("id") or raw_title).strip()
        slug = url_name_to_slug(url_name)
        if doc_type == "release_note":
            product, clean_title, platform = parse_rn_title(raw_title)
        else:
            product, clean_title, platform = parse_kb_title(raw_title)
        content = str(row.get("content") or "").strip()
        chunks, sections = chunk_with_sections(f"{clean_title}\n\n{content}", slug)
        chunked.append({
            "row": row,
            "article_id": f"{id_prefix}_{slug}",
            "clean_title": clean_title,
            "product": product,
            "platform": row_platform(row, platform),
            "chunks": chunks,
            "sections": sections,
            "condition_flags": [detect_condition_flags(chunk["content"], clean_title, chunk.get("heading_path", "")) for chunk in chunks],
        })
    return chunked


def attach_metadata(chunked_rows: list[dict], *, source_category: str, source_type: str, filename: str) -> list[dict]:
    attached = []
    for item in chunked_rows:
        row = item["row"]
        content = str(row.get("content") or "").strip()
        url = str(row.get("url") or "").strip()
        metadata = build_source_metadata(
            source_category=source_category,
            source_type=source_type,
            filename=filename,
            article_id=item["article_id"],
            url=url,
            content=content,
            chunk=content,
        )
        attached.append({**item, "source_metadata": metadata})
    return attached


def write_to_db(vec_cursor, chunk_id: str, embedding_str: str, product: str, platform: str, doc_type: str, identifier_values: tuple) -> None:
    vec_cursor.execute(INSERT_CHUNK, (chunk_id, embedding_str, product, platform, doc_type))
    vec_cursor.execute(INSERT_KB_IDENTIFIER, identifier_values)


def preview_import_summary(path: str, source_key: str, source_settings: dict | None = None) -> dict:
    source_settings = source_settings or {}
    summary = empty_import_summary(source_key, path)
    if not source_settings.get("enabled", True):
        summary["warnings"].append("Source is disabled and will be skipped.")
        return summary
    if source_key == "historical_tickets":
        summary["warnings"].append("Historical tickets are future/offline-only and are skipped.")
        return summary

    raw = pd.read_csv(path, engine="python")
    summary["total_rows_seen"] = len(raw)
    validation = project_config.validate_source_contract(
        source_key,
        list(raw.columns),
        source_settings.get("column_mapping") or {},
    )
    summary["warnings"].extend(validation.get("warnings", []))
    if not validation.get("valid"):
        summary["rows_failed"] = len(raw)
        summary["failed_row_reasons"].extend(validation.get("errors", []))
        return summary

    df = read_csv(path, source_settings)
    for row_idx, row in df.iterrows():
        content = str(row.get("content") or "").strip()
        title = str(row.get("title") or "").strip()
        if not content or len(content.split()) < 5:
            summary["rows_skipped"] += 1
            summary["skipped_row_reasons"].append({
                "row_number": int(row_idx) + 1,
                "reason": "Content is blank or shorter than five words.",
            })
            continue
        slug = url_name_to_slug(str(row.get("url_name") or row.get("id") or title))
        chunks, sections = chunk_with_sections(f"{title}\n\n{content}", slug)
        summary["rows_loaded"] += 1
        summary["chunks_created"] += len(chunks)
        summary["parent_sections_created"] += len(sections)
    return summary


# ── Title parsing ─────────────────────────────────────────────
_KB_APP_PATTERN = re.compile(r'\b(app|mobile)\b', re.IGNORECASE)


def parse_kb_title(raw: str) -> tuple:
    """Extract (product, clean_title, platform) from 'Product | Article Title'."""
    parts = raw.split("|", 1)
    if len(parts) == 2:
        product     = parts[0].strip()
        clean_title = parts[1].strip()
        platform    = "app" if _KB_APP_PATTERN.search(clean_title) else "website"
    else:
        product     = ""
        clean_title = raw.strip()
        platform    = "website"
    return product, clean_title, platform


def parse_rn_title(raw: str) -> tuple:
    """Extract (product, clean_title, platform) from 'Product | WebApp | Date'."""
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) >= 2:
        product        = parts[0]
        platform_label = parts[1]
        platform       = "app" if re.search(r'\bmobile\b', platform_label, re.IGNORECASE) else "website"
        clean_title    = platform_label
    else:
        product     = ""
        clean_title = raw.strip()
        platform    = "website"
    return product, clean_title, platform


def row_platform(row, inferred_platform: str) -> str:
    for field in ("platform", "affected_platform"):
        value = str(row.get(field) or "").strip().lower()
        if value and value != "nan":
            return value
    return inferred_platform


# ── Slug helper ───────────────────────────────────────────────
def url_name_to_slug(url_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", url_name).lower().strip("_")


# ── Smart Chunking ───────────────────────────────────────────
MAX_CHUNK_WORDS = 200


def _is_table(para: str) -> bool:
    """Table preserver: true if paragraph is a markdown table."""
    lines = [l for l in para.splitlines() if l.strip()]
    if len(lines) < 2:
        return False
    pipe_lines = sum(1 for l in lines if "|" in l)
    has_separator = any(re.match(r"^\s*\|[\s\-:]+\|", l) for l in lines)
    return pipe_lines >= 2 and has_separator


def _detect_heading(para: str) -> tuple:
    """Heading detector: return (is_heading, heading_text)."""
    lines = para.splitlines()
    first = lines[0].strip()
    m = re.match(r"^#{1,4}\s+(.+)", first)
    if m:
        return True, m.group(1)
    if len(lines) >= 2 and re.match(r"^={3,}\s*$", lines[1].strip()):
        return True, first
    if len(lines) >= 2 and re.match(r"^-{3,}\s*$", lines[1].strip()) and "|" not in first:
        return True, first
    return False, ""


def _split_at_boundaries(text: str, max_words: int) -> list:
    """Boundary detector: split text at sentence ends to stay under max_words."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current, wc = [], [], 0
    for sent in sentences:
        sw = len(sent.split())
        if wc + sw > max_words and current:
            chunks.append(" ".join(current))
            current, wc = [], 0
        current.append(sent)
        wc += sw
    if current:
        chunks.append(" ".join(current))
    return chunks


def smart_chunk(text: str, max_words: int = MAX_CHUNK_WORDS) -> list:
    """
    Chunk text into semantically coherent pieces.
    Respects table boundaries, heading sections, and paragraph/sentence splits.
    """
    paras = re.split(r"\n{2,}", text.strip())
    chunks = []
    current_heading = ""
    current_parts = []
    current_wc = 0

    def flush():
        nonlocal current_parts, current_wc
        if current_parts:
            chunks.append("\n\n".join(current_parts))
        current_parts, current_wc = [], 0

    for para in paras:
        para = para.strip()
        if not para:
            continue

        if _is_table(para):
            flush()
            header = f"{current_heading}\n\n" if current_heading else ""
            chunks.append(header + para)
            continue

        is_heading, heading_text = _detect_heading(para)
        if is_heading:
            flush()
            current_heading = heading_text
            current_parts = [current_heading]
            current_wc = len(current_heading.split())
            continue

        para_wc = len(para.split())
        if current_wc + para_wc > max_words and current_parts:
            flush()
            if current_heading:
                current_parts = [current_heading]
                current_wc = len(current_heading.split())

        if para_wc > max_words:
            flush()
            for piece in _split_at_boundaries(para, max_words):
                header = f"{current_heading}\n\n" if current_heading else ""
                chunks.append(header + piece)
        else:
            current_parts.append(para)
            current_wc += para_wc

    flush()
    return [c.strip() for c in chunks if c.strip()]


def chunk_with_sections(text: str, article_slug: str, max_words: int = MAX_CHUNK_WORDS) -> tuple[list[dict], list[dict]]:
    """
    Return child chunks with parent section metadata plus parent sections.
    Keeps old smart_chunk behavior available while adding v2.8 parent tracking.
    """
    paras = re.split(r"\n{2,}", text.strip())
    sections = []
    current_heading = ""
    current_parts = []
    current_section_id = f"sec_{article_slug}_root"

    def flush_section():
        nonlocal current_parts
        if current_parts:
            sections.append({
                "id": current_section_id,
                "heading_path": current_heading,
                "section_text": "\n\n".join(current_parts).strip(),
            })
        current_parts = []

    for para in paras:
        para = para.strip()
        if not para:
            continue
        is_heading, heading_text = _detect_heading(para)
        if is_heading:
            flush_section()
            current_heading = heading_text
            current_section_id = f"sec_{article_slug}_{url_name_to_slug(heading_text) or len(sections)}"
            current_parts = [heading_text]
        else:
            current_parts.append(para)
    flush_section()

    if not sections and text.strip():
        sections = [{
            "id": current_section_id,
            "heading_path": "",
            "section_text": text.strip(),
        }]

    chunks = []
    for section in sections:
        for chunk in smart_chunk(section["section_text"], max_words=max_words):
            chunks.append({
                "content": chunk,
                "parent_section_id": section["id"],
                "heading_path": section["heading_path"],
            })
    return chunks, sections


def build_chunk_texts(
    chunk: str,
    *,
    title: str,
    source_type: str,
    heading_path: str = "",
    section_text: str = "",
    product: str = "",
    platform: str = "",
    role_or_permission: str = "",
    version_or_date: str = "",
    known_issue_status: str = "",
    applies_when: str = "",
    contextual_retrieval_enabled: bool = True,
) -> dict:
    """Build separate retrieval and display text with inherited document context."""
    context_parts = build_contextual_retrieval_fields(
        title=title,
        heading_path=heading_path,
        product=product,
        platform=platform,
        source_type=source_type,
        role_or_permission=role_or_permission,
        version_or_date=version_or_date,
        known_issue_status=known_issue_status,
        applies_when=applies_when,
        enabled=contextual_retrieval_enabled,
    )
    if not context_parts:
        context_parts = [
            f"Title: {title}" if title else "",
            f"Source type: {source_type}" if source_type else "",
            f"Section: {heading_path}" if heading_path else "",
        ]
    context = "\n".join(part for part in context_parts if part)
    embedding_text = "\n\n".join(part for part in [context, chunk] if part).strip()
    display_context = "\n".join(part for part in [
        f"Article: {title}" if title else "",
        f"Section: {heading_path}" if heading_path else "",
    ] if part)
    display_text = "\n\n".join(part for part in [display_context, chunk] if part).strip()
    if section_text and len(chunk.split()) < 60:
        embedding_text = "\n\n".join(part for part in [embedding_text, f"Nearby section context: {section_text[:1200]}"] if part)
    return {"embedding_text": embedding_text, "display_text": display_text}


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


# ── Metadata generation (extractive) ─────────────────────────
_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "has", "have", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "this", "that", "these", "those",
    "it", "its", "if", "as", "so", "not", "no", "you", "your", "we",
    "our", "they", "their", "he", "she", "i", "my", "me", "us", "them",
}


def generate_metadata(chunk: str, title: str) -> dict:
    words = re.findall(r"\b[a-zA-Z]{3,}\b", chunk.lower())
    freq = {}
    for w in words:
        if w not in _STOP_WORDS:
            freq[w] = freq.get(w, 0) + 1
    keywords = ", ".join(k for k, _ in sorted(freq.items(), key=lambda x: -x[1])[:8])

    sentences = re.split(r"(?<=[.!?])\s+", chunk.strip())
    summary = " ".join(s for s in sentences[:2] if s)

    return {"summary": summary, "keywords": keywords, "questions": ""}


def detect_chunk_type(chunk: str, title: str, heading: str, doc_type: str, rules_config: dict | None = None) -> str:
    if _is_table(chunk):
        return "table"

    policy_config = rules_config or project_config.load_config("retrieval_policy")
    rules = policy_config.get("chunk_type_rules", {})
    searchable = {
        "heading": (heading or "").lower(),
        "title": (title or "").lower(),
        "content": (chunk or "").lower(),
    }

    matches = []
    for chunk_type, rule in rules.items():
        if not rule.get("enabled", True):
            continue
        negative = [str(k).lower() for k in rule.get("negative_keywords", [])]
        if any(k and k in searchable["content"] for k in negative):
            continue
        heading_keywords = [str(k).lower() for k in rule.get("heading_keywords", [])]
        content_keywords = [str(k).lower() for k in rule.get("content_keywords", [])]
        heading_match = any(k and (k in searchable["heading"] or k in searchable["title"]) for k in heading_keywords)
        content_match = any(k and k in searchable["content"] for k in content_keywords)
        if heading_match or content_match:
            matches.append((int(rule.get("priority", 0) or 0), chunk_type))

    if matches:
        return sorted(matches, reverse=True)[0][1]
    if doc_type == "release_note":
        return "release_change"
    return "concept"


def detect_condition_flags(chunk: str, title: str = "", heading: str = "") -> list[str]:
    text = " ".join([heading or "", title or "", chunk or ""]).lower()
    checks = [
        ("requires_setting", [
            "only if", "if enabled", "must enable", "requires", "required",
            "setting", "settings", "configure", "configuration", "enabled",
        ]),
        ("requires_role", ["role", "admin", "administrator", "manager", "employee"]),
        ("requires_permission", ["permission", "permissions", "access level", "authorized", "privilege"]),
        ("requires_feature_enabled", ["feature enabled", "feature flag", "enable the feature", "module enabled"]),
        ("platform_specific", ["web", "website", "browser", "mobile", "ios", "android", "app only", "web only"]),
        ("plan_specific", ["plan", "tier", "subscription", "package"]),
        ("version_or_date_specific", ["version", "release", "as of", "starting in", "deprecated"]),
        ("policy_exception", ["policy", "exception", "eligibility", "eligible", "not eligible"]),
        ("account_specific", ["account setting", "account configuration", "tenant", "workspace", "location"]),
    ]
    flags = []
    for flag, keywords in checks:
        if any(keyword in text for keyword in keywords):
            flags.append(flag)
    if re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b20\d{2}\b", text):
        flags.append("version_or_date_specific")
    return sorted(set(flags))


# ── File type detection ───────────────────────────────────────
def detect_file_type(filename: str) -> str:
    return "release_note" if "release_note" in filename.lower() else "knowledge_base"


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _source_tier(source_type: str) -> str:
    if source_type == "policy":
        return "canonical_policy"
    if source_type in {"official_help_article", "knowledge_base", "faq"}:
        return "approved_kb"
    if source_type == "release_note":
        return "approved_release_note"
    if source_type == "known_issue":
        return "approved_conditional"
    return "unreviewed"


def build_source_metadata(
    *,
    source_category: str,
    source_type: str,
    filename: str,
    article_id: str,
    url: str,
    content: str,
    chunk: str,
    document_version: int = 1,
    chunk_version: int = 1,
) -> dict:
    raw_or_future = source_category == "historical_tickets" or source_type.startswith("raw_")
    source_ref = url or filename
    now = datetime.now(timezone.utc).isoformat()
    return {
        "source_id": f"{source_category}:{article_id}",
        "is_approved": not raw_or_future,
        "tier": _source_tier(source_type),
        "source_ref": source_ref,
        "lineage_ref": article_id,
        "reviewed_by": "demo_seed" if not raw_or_future else "",
        "approved_at": now if not raw_or_future else "",
        "expires_at": "",
        "needs_review_at": "",
        "audience_allowed": ["customer", "internal"] if not raw_or_future else ["internal"],
        "source_category": source_category,
        "is_customer_facing_allowed": not raw_or_future,
        "is_internal_only": raw_or_future,
        "is_future_only": raw_or_future,
        "source_url": url,
        "document_id": article_id,
        "document_version": document_version,
        "chunk_version": chunk_version,
        "document_hash": _sha256_text(content),
        "chunk_hash": _sha256_text(chunk),
        "is_active": True,
        "superseded_by_chunk_id": "",
        "superseded_at": "",
        "superseded_reason": "",
        "active_from": now,
        "active_until": "",
        "updated_at": now,
        **redaction_status(chunk, chunk),
        "ingested_at": now,
        "loader_version": LOADER_VERSION,
        "config_hash": project_config.runtime_fingerprint(),
        "disabled": False,
        "source_license": "",
        "attribution_required": False,
        "attribution_text": "",
    }


def _ingestion_redaction_enabled(policy_config: dict) -> bool:
    pii = policy_config.get("privacy", {}).get("pii_redaction", {})
    return bool(pii.get("enabled", True))


# ── Load one CSV ──────────────────────────────────────────────
def load_csv(
    path: str,
    vec_conn,
    ops_conn,
    loaded_ids: set,
    existing_document_hashes: dict[str, str] | None = None,
    existing_document_versions: dict[str, int] | None = None,
) -> tuple:
    """
    Loads one CSV into the knowledge schema.
    Product and platform are extracted automatically from the title column.

    Returns (inserted, skipped_chunks, skipped_articles, failed).
    """
    filename  = os.path.basename(path)
    source_config = project_config.load_config("sources")
    policy_config = project_config.load_config("retrieval_policy")
    source_category, source_settings = project_config.get_source_category(filename, source_config)
    if source_settings and not source_settings.get("enabled", True):
        print(f"\n  Skipping disabled source category: {filename} [{source_category}]", flush=True)
        LAST_IMPORT_SUMMARIES.append({
            **empty_import_summary(source_category, path),
            "rows_skipped": 0,
            "warnings": ["Source is disabled and was skipped."],
        })
        return 0, 0, 0, []
    if source_category == "historical_tickets":
        print(f"\n  Skipping future/offline-only source category: {filename} [{source_category}]", flush=True)
        LAST_IMPORT_SUMMARIES.append({
            **empty_import_summary(source_category, path),
            "warnings": ["Historical tickets are future/offline-only and were skipped."],
        })
        return 0, 0, 0, []

    file_type = detect_file_type(filename)
    doc_type  = "release_note" if source_category == "release_notes" or file_type == "release_note" else source_category
    source_type = source_settings.get("source_type") or doc_type
    source_authority = project_config.get_source_authority(source_type, policy_config)
    id_prefix = "rn" if file_type == "release_note" else "kb"
    existing_document_hashes = existing_document_hashes or {}
    existing_document_versions = existing_document_versions or {}

    print(f"\n  Reading: {filename}  [{file_type}]", flush=True)
    df = read_csv(path, source_settings)
    print(f"  Articles: {len(df)}", flush=True)

    summary = empty_import_summary(source_category, path)
    summary["total_rows_seen"] = len(df)
    inserted         = 0
    skipped_chunks   = 0
    skipped_articles = 0
    failed           = []
    vec_cursor       = vec_conn.cursor()
    total            = len(df)
    start_t          = time.time()

    for i, row in df.iterrows():
        idx     = i + 1
        filled  = int(30 * idx / total)
        bar     = "█" * filled + "░" * (30 - filled)
        elapsed = time.time() - start_t
        eta_s   = int(elapsed / idx * (total - idx)) if idx > 1 else 0
        eta_str = f"{eta_s // 60}m {eta_s % 60:02d}s" if idx > 1 else "--:--"
        print(f"\r  [{bar}] {idx}/{total}  ETA {eta_str}  ", end="", flush=True)

        raw_title = str(row["title"]).strip()
        url_name  = str(row["url_name"]).strip()
        url       = str(row["url"]).strip()
        original_content = str(row["content"]).strip()
        content = original_content
        if _ingestion_redaction_enabled(policy_config):
            content = redact_text(content)
        if not url_name:
            url_name = str(row["id"]).strip() or raw_title
        slug      = url_name_to_slug(url_name)

        if file_type == "release_note":
            product, clean_title, platform = parse_rn_title(raw_title)
        else:
            product, clean_title, platform = parse_kb_title(raw_title)
        product = project_config.canonical_product_for_ingestion(str(row.get("product") or product or ""))
        platform = row_platform(row, platform)

        if not content or len(content.split()) < 5:
            failed.append(url_name)
            summary["rows_failed"] += 1
            summary["failed_row_reasons"].append({
                "row_number": int(idx),
                "reason": "Content is blank or shorter than five words.",
                "row_id": url_name,
            })
            continue

        full_text = f"{clean_title}\n\n{content}"
        chunks, sections = chunk_with_sections(full_text, slug)
        article_id = f"{id_prefix}_{slug}"
        sections_by_id = {section["id"]: section for section in sections}

        chunk_ids = [f"{id_prefix}_{slug}_{j}" for j in range(len(chunks))]
        document_hash = _sha256_text(content)
        document_version = next_document_version(article_id, document_hash, existing_document_hashes, existing_document_versions)
        if document_version > 1:
            chunk_ids = [f"{article_id}_v{document_version}_{j}" for j in range(len(chunks))]
        reingestion_plan = plan_document_reingestion(
            article_id=article_id,
            document_hash=document_hash,
            desired_chunk_ids=chunk_ids,
            existing_document_hashes=existing_document_hashes,
            loaded_ids=loaded_ids,
        )
        if reingestion_plan["action"] == "skip_unchanged":
            skipped_articles += 1
            skipped_chunks   += len(chunks)
            summary["rows_skipped"] += 1
            summary["skipped_row_reasons"].append({
                "row_number": int(idx),
                "reason": "Document hash unchanged; chunks already exist in the vector DB.",
                "row_id": url_name,
            })
            continue
        if reingestion_plan["action"] == "upsert_changed":
            tombstone_existing_document_chunks(
                vec_cursor,
                article_id=article_id,
                superseded_by_chunk_ids=reingestion_plan["chunks_to_upsert"],
            )

        for section in sections:
            vec_cursor.execute(INSERT_ARTICLE_SECTION, (
                section["id"], article_id, clean_title,
                section["heading_path"], section["section_text"],
                product, platform, doc_type, source_type, url, filename,
            ))
            summary["parent_sections_created"] += 1

        for chunk_idx, chunk_data in enumerate(chunks):
            chunk_id = chunk_ids[chunk_idx]
            chunk = chunk_data["content"]
            section = sections_by_id.get(chunk_data.get("parent_section_id", ""), {})
            retrieval_settings = policy_config.get("retrieval", {})
            chunk_texts = build_chunk_texts(
                chunk,
                title=clean_title,
                source_type=source_type,
                heading_path=chunk_data.get("heading_path", ""),
                section_text=section.get("section_text", ""),
                product=product,
                platform=platform,
                role_or_permission=str(row.get("role") or row.get("permission") or "").strip(),
                version_or_date=str(row.get("version") or row.get("release_date") or "").strip(),
                known_issue_status=str(row.get("status") or "").strip(),
                applies_when=str(row.get("applies_when") or row.get("workaround") or "").strip(),
                contextual_retrieval_enabled=bool(retrieval_settings.get("contextual_retrieval", {}).get("enabled", True)),
            )
            embedding     = get_embedding(chunk_texts["embedding_text"])
            embedding_str = "[" + ",".join(map(str, embedding)) + "]"
            meta          = generate_metadata(chunk, clean_title)
            chunk_type    = detect_chunk_type(
                chunk, clean_title, chunk_data.get("heading_path", ""), doc_type, policy_config
            )
            condition_flags = detect_condition_flags(chunk, clean_title, chunk_data.get("heading_path", ""))
            source_metadata = build_source_metadata(
                source_category=source_category,
                source_type=source_type,
                filename=filename,
                article_id=article_id,
                url=url,
                content=content,
                chunk=chunk,
                document_version=document_version,
                chunk_version=chunk_idx + 1,
            )
            source_metadata.update(redaction_status(original_content, content))
            source_metadata["source_license"] = str(row.get("source_license") or "").strip()
            source_metadata["attribution_required"] = str(row.get("attribution_required") or "").strip().lower() in {"1", "true", "yes", "required"}
            source_metadata["attribution_text"] = str(row.get("attribution_text") or "").strip()

            write_to_db(vec_cursor, chunk_id, embedding_str, product, platform, doc_type, (
                chunk_id, clean_title, url_name, url, chunk,
                chunk_texts["embedding_text"], chunk_texts["display_text"], article_id,
                chunk_idx, len(chunks), filename,
                meta["summary"], meta["keywords"], meta["questions"],
                chunk_type, chunk_data.get("parent_section_id", ""),
                chunk_data.get("heading_path", ""), source_type,
                source_metadata["source_id"], source_metadata["document_id"],
                source_metadata["document_version"], source_metadata["chunk_version"],
                source_metadata["is_approved"],
                source_metadata["tier"], source_metadata["source_ref"],
                source_metadata["lineage_ref"], source_metadata["reviewed_by"],
                source_metadata["approved_at"], source_metadata["expires_at"],
                source_metadata["needs_review_at"], json.dumps(source_metadata["audience_allowed"]),
                source_metadata["source_category"], source_metadata["is_customer_facing_allowed"],
                source_metadata["is_internal_only"], source_metadata["is_future_only"],
                source_metadata["source_url"], source_metadata["document_hash"],
                source_metadata["chunk_hash"], source_metadata["is_active"],
                source_metadata["superseded_by_chunk_id"], source_metadata["superseded_at"],
                source_metadata["superseded_reason"], source_metadata["active_from"],
                source_metadata["active_until"], source_metadata["updated_at"],
                source_metadata["redaction_status"], source_metadata["redaction_applied"],
                source_metadata["ingested_at"],
                source_metadata["loader_version"], source_metadata["config_hash"],
                source_metadata["disabled"], source_authority,
                json.dumps(condition_flags),
                source_metadata["source_license"],
                source_metadata["attribution_required"],
                source_metadata["attribution_text"],
            ))
            inserted += 1
            summary["chunks_created"] += 1
            loaded_ids.add(chunk_id)

        summary["rows_loaded"] += 1
        existing_document_hashes[article_id] = document_hash
        existing_document_versions[article_id] = document_version

        vec_conn.commit()

    vec_cursor.close()
    LAST_IMPORT_SUMMARIES.append(summary)
    print()
    return inserted, skipped_chunks, skipped_articles, failed


# ── Main ──────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  Knowledge Base Loader")
    print("=" * 50)
    print()

    if not os.path.exists(PROCESSED_DIR):
        print(f"  No processed/ folder found at:\n  {PROCESSED_DIR}")
        print("  Run kb_scraper.py first to generate CSV files.")
        return

    csv_paths = loader_source_paths()

    if not csv_paths:
        print("  No loadable CSV files found.")
        if not config.DEMO_MODE:
            print("  DEMO_MODE=false blocks committed demo CSVs.")
            print("  Set DEMO_MODE=true for sandbox data, or configure custom source paths.")
        else:
            print(f"  Run kb_scraper.py first or add CSV files to:\n  {PROCESSED_DIR}")
        return

    print(f"  Demo mode: {'on' if config.DEMO_MODE else 'off'}")
    print(f"  Found {len(csv_paths)} CSV file(s):\n")
    for i, path in enumerate(csv_paths, 1):
        f = os.path.basename(path)
        ftype = "release notes -> knowledge_base [doc_type=release_note]" if "release_note" in f \
                else "knowledge base → knowledge_base [doc_type=knowledge_base]"
        print(f"  {i}. {f}  [{ftype}]")
    print()

    print("  Which files to load?")
    print("  Enter number(s) separated by comma, or 'all'")
    print()
    choice = input("  > ").strip().lower()
    print()

    if choice == "all":
        selected = csv_paths
    else:
        try:
            indices  = [int(x.strip()) - 1 for x in choice.split(",")]
            selected = [csv_paths[i] for i in indices]
        except (ValueError, IndexError):
            print("  Invalid selection.")
            return

    print("  Connecting to knowledge schema...", flush=True)
    try:
        vec_conn = get_vec_connection()
        ensure_vector_schema(vec_conn, schema=config.KNOWLEDGE_SCHEMA)
        print(f"  knowledge schema connected ({config.KNOWLEDGE_SCHEMA}).\n", flush=True)
    except Exception as e:
        print(f"  knowledge schema connection failed: {e}")
        return

    print("  Connecting to ops schema...", flush=True)
    try:
        ops_conn = get_ops_connection()
        ensure_ops_schema(ops_conn, schema=config.OPS_SCHEMA)
        print(f"  ops schema connected ({config.OPS_SCHEMA}).\n", flush=True)
    except Exception as e:
        print(f"  ops schema connection failed: {e}")
        vec_conn.close()
        return

    print("  Checking existing DB entries...", flush=True)
    loaded_ids = get_loaded_ids(vec_conn)
    existing_document_hashes = get_loaded_document_hashes(vec_conn)
    existing_document_versions = get_loaded_document_versions(vec_conn)
    print(f"  {len(loaded_ids)} chunks already in DB\n", flush=True)

    total_inserted         = 0
    total_skipped_chunks   = 0
    total_skipped_articles = 0
    total_failed           = []

    for path in selected:
        filename = os.path.basename(path)
        inserted, skipped_chunks, skipped_articles, failed = load_csv(
            path, vec_conn, ops_conn, loaded_ids, existing_document_hashes, existing_document_versions,
        )
        total_inserted         += inserted
        total_skipped_chunks   += skipped_chunks
        total_skipped_articles += skipped_articles
        total_failed           += failed
        print(f"  Inserted: {inserted}  "
              f"Skipped articles: {skipped_articles}  "
              f"Skipped chunks: {skipped_chunks}  "
              f"Failed: {len(failed)}")

    # Rebuild the ivfflat index now that data is loaded.
    # If the index was created on an empty table (e.g. after rebuild_db),
    # the centroids are wrong. REINDEX recomputes them from the actual vectors.
    if total_inserted > 0:
        print("\n  Rebuilding vector index...", flush=True)
        try:
            with vec_conn.cursor() as cur:
                cur.execute("REINDEX INDEX knowledge_base_embedding_idx;")
            vec_conn.commit()
            print("  Vector index rebuilt.\n", flush=True)
        except Exception as e:
            print(f"  Index rebuild failed (non-fatal): {e}\n", flush=True)

    vec_conn.close()
    ops_conn.close()

    print(f"\n{'=' * 50}")
    print(f"  Load Complete")
    print(f"{'=' * 50}")
    print(f"  Total inserted:         {total_inserted}")
    print(f"  Articles skipped:       {total_skipped_articles}  (fully in DB)")
    print(f"  Chunks skipped:         {total_skipped_chunks}  (already in DB)")
    print(f"  Total failed:           {len(total_failed)}")
    if total_failed:
        print("\n  Failed articles:")
        for f in total_failed:
            print(f"    x {f}")


if __name__ == "__main__":
    main()
