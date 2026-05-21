import psycopg2
import json
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from rank_bm25 import BM25Okapi
from backend.providers.embedding_model import get_embedding
from backend.core.logger import get_logger
from backend.db.schema import SEMANTIC_SEARCH, FETCH_ALL_FOR_BM25, FETCH_PARENT_SECTIONS, FETCH_NEIGHBOR_CHUNKS, GET_CHUNK_BY_ID
from backend.db.schema import _safe_schema_name
from pipeline.retrieval_policy import score_candidate_with_policy, merge_by_source_type
from pipeline.cache import get_cached_chunks, save_cached_chunks, hash_key
from backend.core import config, project_config

logger = get_logger(__name__)

_pool: ThreadedConnectionPool | None = None
RETRIEVAL_CACHE_SCHEMA_VERSION = "retrieval-cache-v3-redaction-role-filter-fix"


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(2, 5, config.DATABASE_URL)
        logger.info("DB connection pool initialised (min=2 max=5)")
    return _pool


def _reset_pool() -> None:
    global _pool
    try:
        if _pool is not None:
            _pool.closeall()
    except Exception:
        pass
    _pool = None


def get_db_connection():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.rollback()
        schema = _safe_schema_name(config.KNOWLEDGE_SCHEMA)
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public;')
    except Exception:
        # Connection is broken — discard it, reset pool so next call gets a fresh one
        _reset_pool()
        raise
    return conn


def release_db_connection(conn) -> None:
    try:
        if not conn.closed:
            conn.rollback()
        _get_pool().putconn(conn)
    except Exception:
        pass


def _normalize_rows(rows: list[dict]) -> list[dict]:
    policy_config = project_config.load_config("retrieval_policy")
    normalized = []
    for row in rows:
        item = dict(row)
        source_type = item.get("source_type") or item.get("doc_type") or "knowledge_base"
        try:
            item["source_authority"] = project_config.get_source_authority(source_type, policy_config)
        except (TypeError, ValueError):
            item["source_authority"] = 1.0
        flags = item.get("condition_flags", [])
        if isinstance(flags, str):
            try:
                flags = json.loads(flags) if flags.strip() else []
            except json.JSONDecodeError:
                flags = [f.strip() for f in flags.split(",") if f.strip()]
        item["condition_flags"] = flags if isinstance(flags, list) else []
        audience = item.get("audience_allowed", [])
        if isinstance(audience, str):
            try:
                audience = json.loads(audience) if audience.strip() else []
            except json.JSONDecodeError:
                audience = [a.strip() for a in audience.strip("[]").replace('"', "").split(",") if a.strip()]
        item["audience_allowed"] = audience if isinstance(audience, list) else []
        for key in ["is_approved", "is_customer_facing_allowed", "is_internal_only", "is_future_only", "disabled", "redaction_applied"]:
            value = item.get(key, False)
            if isinstance(value, str):
                item[key] = value.strip().lower() in {"1", "true", "yes", "approved"}
            else:
                item[key] = bool(value)
        if item.get("display_text"):
            item.setdefault("content", item["display_text"])
        item.setdefault("retrieval_reason", "initial_match")
        item.setdefault("article_id", "")
        normalized.append(item)
    return normalized


_SAFETY_METADATA_FIELDS = (
    "source_id",
    "source_type",
    "source_category",
    "tier",
    "source_ref",
    "lineage_ref",
    "reviewed_by",
    "approved_at",
    "audience_allowed",
    "is_customer_facing_allowed",
    "is_internal_only",
    "is_future_only",
    "source_url",
    "document_hash",
    "chunk_hash",
    "updated_at",
    "redaction_status",
    "redaction_applied",
    "ingested_at",
    "loader_version",
    "config_hash",
    "disabled",
    "source_authority",
    "condition_flags",
)


def _missing_safety_metadata(chunk: dict) -> bool:
    return any(chunk.get(field) in (None, "", [], {}) for field in _SAFETY_METADATA_FIELDS)


def _hydrate_cached_chunks(chunks: list[dict], conn) -> list[dict]:
    if not chunks or not any(_missing_safety_metadata(chunk) for chunk in chunks):
        return chunks

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        hydrated = []
        by_id: dict[str, dict] = {}
        for chunk in chunks:
            chunk_id = str(chunk.get("id") or "").strip()
            if not chunk_id:
                hydrated.append(chunk)
                continue
            if chunk_id not in by_id:
                cursor.execute(GET_CHUNK_BY_ID, (chunk_id,))
                row = cursor.fetchone()
                rows = _normalize_rows([dict(row)]) if row else []
                by_id[chunk_id] = rows[0] if rows else {}
            db_chunk = by_id[chunk_id]
            if not db_chunk:
                hydrated.append(chunk)
                continue

            merged = {**db_chunk, **chunk}
            for field in _SAFETY_METADATA_FIELDS:
                if merged.get(field) in (None, "", [], {}):
                    merged[field] = db_chunk.get(field)
            if not merged.get("updated_at"):
                merged["updated_at"] = (
                    merged.get("ingested_at")
                    or merged.get("approved_at")
                    or db_chunk.get("updated_at")
                    or db_chunk.get("ingested_at")
                    or db_chunk.get("approved_at")
                    or ""
                )
            hydrated.append(merged)
        return _normalize_rows(hydrated)
    finally:
        cursor.close()


# ── Semantic Search ──────────────────────────────────────────
def semantic_search(query: str, top_k: int, conn, product_values: list[str], platform: str) -> list:
    try:
        try:
            embedding = get_embedding(query)
        except Exception as e:
            logger.warning(f"Embedding failed — skipping semantic search: {e}")
            return []

        embedding_str = "[" + ",".join(map(str, embedding)) + "]"

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(SEMANTIC_SEARCH, (embedding_str, product_values, platform, embedding_str, top_k))
        results = cursor.fetchall()
        cursor.close()

        return _normalize_rows([dict(r) for r in results])

    except Exception as e:
        logger.error(f"Semantic search failed: {e}")
        raise ValueError(f"Semantic search failed: {e}")


# ── Keyword Search ───────────────────────────────────────────
def keyword_search(query: str, top_k: int, conn, product_values: list[str], platform: str) -> list:
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(FETCH_ALL_FOR_BM25, (product_values, platform))
        rows = _normalize_rows([dict(r) for r in cursor.fetchall()])
        cursor.close()

        if not rows:
            return []

        tokenized_corpus = [(row.get("embedding_text") or row.get("content", "")).lower().split() for row in rows]
        bm25 = BM25Okapi(tokenized_corpus)

        tokenized_query = query.lower().split()
        scores = bm25.get_scores(tokenized_query)

        for i, row in enumerate(rows):
            row["score"] = float(scores[i])

        ranked = sorted(rows, key=lambda x: x["score"], reverse=True)
        return ranked[:top_k]

    except Exception as e:
        logger.error(f"Keyword search failed: {e}")
        raise ValueError(f"Keyword search failed: {e}")


# ── RRF ──────────────────────────────────────────────────────
def reciprocal_rank_fusion(semantic: list, keyword: list, top_k: int) -> list:
    try:
        k = 60
        scores = {}
        data = {}

        for rank, result in enumerate(semantic):
            doc_id = result["id"]
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank + 1)
            data[doc_id] = result

        for rank, result in enumerate(keyword):
            doc_id = result["id"]
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank + 1)
            if doc_id not in data:
                data[doc_id] = result

        ranked_ids = sorted(scores, key=lambda x: scores[x], reverse=True)

        merged = []
        for doc_id in ranked_ids[:top_k]:
            result = data[doc_id]
            merged.append({**result, "rrf_score": round(scores[doc_id], 6)})

        return merged

    except Exception as e:
        logger.error(f"RRF fusion failed: {e}")
        raise ValueError(f"RRF fusion failed: {e}")


def apply_route_policy(chunks: list, route: str, top_k: int) -> list:
    scored = [score_candidate_with_policy(chunk, route) for chunk in chunks]
    allowed = [chunk for chunk in scored if not chunk.get("policy_disallowed")]
    if len(allowed) < len(scored):
        logger.info(f"Route policy removed {len(scored) - len(allowed)} unsafe/disallowed chunks")
    ranked = sorted(allowed, key=lambda x: x.get("policy_score", 0.0), reverse=True)
    return ranked[:top_k]


def apply_metadata_filter(chunks: list[dict], metadata_filter: dict) -> list[dict]:
    if not metadata_filter:
        return chunks
    role = str(metadata_filter.get("role") or "").strip().lower()
    plan_tier = str(metadata_filter.get("plan_tier") or "").strip().lower()
    product_version = str(metadata_filter.get("product_version") or "").strip().lower()
    channel = str(metadata_filter.get("channel") or "").strip().lower()

    filtered = []
    for chunk in chunks:
        searchable = " ".join([
            str(chunk.get("content") or ""),
            str(chunk.get("embedding_text") or ""),
            str(chunk.get("heading_path") or ""),
            str(chunk.get("title") or ""),
        ]).lower()
        if plan_tier and not _metadata_term_allowed(plan_tier, searchable):
            continue
        if product_version and product_version not in searchable:
            continue
        if channel and channel not in {"web", "website", "mobile app", "app"} and channel not in searchable:
            continue
        filtered.append(chunk)
    return filtered or chunks


def _metadata_term_allowed(term: str, searchable: str) -> bool:
    if not term:
        return True
    return term in searchable or not searchable


def _dedupe_chunks(chunks: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for chunk in chunks:
        chunk_id = chunk.get("id")
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        deduped.append(chunk)
    return deduped


def _retrieval_strategy(workflow: dict, requested_arm: str = "") -> dict:
    experiment = workflow.get("experiments", {}).get("retrieval_strategy_v1", {})
    arm = str(experiment.get("arm") or "current_hybrid_rag")
    allowed = set(experiment.get("allowed_arms") or [])
    if allowed and arm not in allowed:
        arm = "current_hybrid_rag"
    requested = str(requested_arm or "").strip()
    active_arm = requested if requested and (not allowed or requested in allowed) else arm
    if active_arm == "graphrag_layer" and not experiment.get("graphrag_enabled", False):
        return {
            "arm": arm,
            "requested_arm": requested,
            "active_arm": "disabled",
            "enabled": False,
            "reason": "GraphRAG experiment arm is defined but disabled until offline eval proves value.",
        }
    return {
        "arm": arm,
        "requested_arm": requested,
        "active_arm": active_arm,
        "enabled": True,
        "reason": "",
    }


def _question_queries(context: dict, base_query: str, enabled: bool) -> list[dict]:
    if not enabled:
        return [{"question_id": "q0", "query": base_query, "source": "base_query"}]
    plan = context.get("planner_output", {}).get("retrieval_plan") or []
    queries = []
    for item in plan:
        query = str(item.get("query") or "").strip()
        if query:
            queries.append({
                "question_id": str(item.get("question_id") or f"q{len(queries) + 1}"),
                "query": query,
                "source": "planner",
            })
    if not queries:
        queries.append({"question_id": "q0", "query": base_query, "source": "base_query"})
    if base_query and all(item["query"] != base_query for item in queries):
        queries.insert(0, {"question_id": "q0", "query": base_query, "source": "base_query"})
    return queries[:4]


def _retrieve_for_query(
    query: str,
    *,
    conn,
    product_values: list[str],
    platform: str,
    route: str,
    metadata_filter: dict,
    top_k: int,
) -> tuple[list[dict], dict]:
    semantic_results = semantic_search(query, top_k, conn, product_values, platform)
    keyword_results = keyword_search(query, top_k, conn, product_values, platform)
    merged = reciprocal_rank_fusion(semantic_results, keyword_results, top_k)
    filtered = apply_metadata_filter(merged, metadata_filter)
    routed = apply_route_policy(filtered, route, top_k)
    typed = merge_by_source_type(routed, route, top_k)
    return typed, {
        "query": query,
        "semantic_results": semantic_results,
        "keyword_results": keyword_results,
        "rrf_results": merged,
        "metadata_filtered_results": filtered,
        "source_type_merge_results": typed,
        "result_count": len(typed),
    }


def expand_neighbor_chunks(chunks: list, conn) -> list:
    if not chunks:
        return chunks

    policy_config = project_config.load_config("retrieval_policy")
    retrieval_settings = policy_config.get("retrieval", {})
    sibling_enabled = retrieval_settings.get("sibling_expansion", True)
    condition_enabled = retrieval_settings.get("condition_neighbor_expansion", True)
    if not sibling_enabled and not condition_enabled:
        return chunks

    expanded = list(chunks)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        for chunk in chunks:
            article_id = chunk.get("article_id") or ""
            chunk_index = chunk.get("chunk_index")
            if not article_id or chunk_index is None:
                continue

            reasons = []
            span = 0
            if sibling_enabled:
                reasons.append("sibling")
                span = max(span, 1)
            if condition_enabled and chunk.get("condition_flags"):
                reasons.append("condition_neighbor")
                span = max(span, 2)
            if span <= 0:
                continue

            cursor.execute(FETCH_NEIGHBOR_CHUNKS, (article_id, int(chunk_index) - span, int(chunk_index) + span))
            neighbors = _normalize_rows([dict(row) for row in cursor.fetchall()])
            for neighbor in neighbors:
                if neighbor.get("id") == chunk.get("id"):
                    continue
                neighbor.setdefault("rrf_score", chunk.get("rrf_score", 0.0))
                neighbor["policy_score"] = chunk.get("policy_score", chunk.get("rrf_score", 0.0))
                neighbor["retrieval_reason"] = "+".join(reasons)
                neighbor["expanded_from"] = chunk.get("id")
                neighbor["expansion_trace"] = {
                    "type": "neighbor",
                    "reasons": reasons,
                    "expanded_from": chunk.get("id"),
                    "base_word_count": len(str(chunk.get("content", "")).split()),
                    "expanded_word_count": len(str(neighbor.get("content", "")).split()),
                }
                expanded.append(neighbor)
    finally:
        cursor.close()

    return _dedupe_chunks(expanded)


def expand_parent_sections(chunks: list, conn) -> list:
    if not chunks:
        return chunks

    policy_config = project_config.load_config("retrieval_policy")
    retrieval_settings = policy_config.get("retrieval", {})
    if not retrieval_settings.get("parent_section_expansion", True):
        return chunks
    max_ratio = float(retrieval_settings.get("max_expansion_ratio", 2.0) or 2.0)

    parent_ids = sorted({
        chunk.get("parent_section_id")
        for chunk in chunks
        if chunk.get("parent_section_id")
    })
    if not parent_ids:
        return chunks

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(FETCH_PARENT_SECTIONS, (parent_ids,))
    parents = {row["id"]: dict(row) for row in cursor.fetchall()}
    cursor.close()

    expanded = []
    for chunk in chunks:
        parent = parents.get(chunk.get("parent_section_id"))
        if parent:
            enriched = dict(chunk)
            enriched["child_content"] = chunk.get("content", "")
            parent_text = parent.get("section_text") or chunk.get("content", "")
            base_words = len(str(chunk.get("content", "")).split())
            parent_words = parent_text.split()
            cap = max(base_words, int(base_words * max_ratio)) if base_words else len(parent_words)
            capped = len(parent_words) > cap
            if capped:
                parent_text = " ".join(parent_words[:cap])
            enriched["content"] = parent_text
            enriched["display_text"] = parent_text
            enriched["retrieval_reason"] = (
                enriched.get("retrieval_reason", "initial_match") + "+parent_section"
            )
            enriched["parent_section"] = {
                "id": parent.get("id", ""),
                "title": parent.get("title", ""),
                "heading_path": parent.get("heading_path", ""),
            }
            enriched["expansion_trace"] = {
                "type": "parent_section",
                "reasons": ["parent_section"],
                "expanded_from": chunk.get("id"),
                "base_word_count": base_words,
                "expanded_word_count": len(parent_text.split()),
                "max_expansion_ratio": max_ratio,
                "capped": capped,
            }
            expanded.append(enriched)
        else:
            expanded.append(chunk)
    return expanded


# ── MAIN ─────────────────────────────────────────────────────
def run(context: dict) -> dict:
    try:
        if not context:
            raise ValueError("Context is missing")

        search_query = context.get("search_query")
        if not search_query:
            raise ValueError("Search query missing")

        product  = context.get("product", "")
        platform = context.get("platform", "")
        if not product or not platform:
            logger.warning("product or platform missing — returning empty retrieval")
            context["retrieved_chunks"] = []
            context["retrieval_cache_hit"] = False
            return context

        product_values = project_config.product_values_for_retrieval(product)
        route = context.get("routing_strategy", "general")
        metadata_filter = context.get("metadata_filter", {})
        workflow = project_config.workflow_settings()
        request_meta = context.get("request_meta", {})
        strategy = _retrieval_strategy(workflow, requested_arm=request_meta.get("experiment_arm", ""))
        advanced = workflow.get("experiments", {}).get("advanced_reasoning", {})
        multi_query_enabled = bool(
            advanced.get("enabled", False)
            and advanced.get("multi_query_retrieval", False)
            and strategy.get("active_arm") == "current_rag_query_decomposition"
        )
        if strategy.get("active_arm") == "disabled":
            logger.warning(strategy.get("reason"))
            context["retrieved_chunks"] = []
            context["retrieval_cache_hit"] = False
            context["retrieval_strategy"] = strategy
            return context

        cache_key = hash_key(
            f"{search_query}|{product}|{platform}|{route}|{metadata_filter}|"
            f"{strategy.get('active_arm')}|{project_config.retrieval_fingerprint()}|"
            f"{RETRIEVAL_CACHE_SCHEMA_VERSION}"
        )
        cached = get_cached_chunks(cache_key)

        if cached:
            logger.info("⚡ RETRIEVAL CACHE HIT")
            normalized_cached = _normalize_rows(cached)
            if any(_missing_safety_metadata(chunk) for chunk in normalized_cached):
                try:
                    conn = get_db_connection()
                    try:
                        normalized_cached = _hydrate_cached_chunks(normalized_cached, conn)
                    finally:
                        release_db_connection(conn)
                except Exception as e:
                    logger.warning(f"Retrieval cache metadata hydration failed — using cached rows: {e}")
            context["retrieved_chunks"] = normalized_cached
            context["retrieval_cache_hit"] = True
            context["retrieval_strategy"] = strategy
            return context

        top_k = config.TOP_K_RETRIEVAL

        try:
            conn = get_db_connection()
        except Exception as e:
            logger.error(f"Vector DB connection failed — returning empty retrieval: {e}", exc_info=True)
            context["retrieved_chunks"] = []
            context["retrieval_cache_hit"] = False
            return context

        try:
            retrieval_trace = []
            merged = []
            for item in _question_queries(context, search_query, multi_query_enabled):
                per_query, trace = _retrieve_for_query(
                    item["query"],
                    conn=conn,
                    product_values=product_values,
                    platform=platform,
                    route=route,
                    metadata_filter=metadata_filter,
                    top_k=top_k,
                )
                for chunk in per_query:
                    chunk["retrieval_question_id"] = item["question_id"]
                    chunk["retrieval_question"] = item["query"]
                    if item["source"] == "planner":
                        chunk["retrieval_reason"] = "decomposed_question"
                trace["question_id"] = item["question_id"]
                trace["source"] = item["source"]
                retrieval_trace.append(trace)
                merged.extend(per_query)
            merged = _dedupe_chunks(merged)
            context["semantic_results"] = retrieval_trace[0]["semantic_results"] if retrieval_trace else []
            context["keyword_results"] = retrieval_trace[0]["keyword_results"] if retrieval_trace else []
            context["rrf_results"] = retrieval_trace[0]["rrf_results"] if retrieval_trace else []
            context["metadata_filtered_results"] = retrieval_trace[0]["metadata_filtered_results"] if retrieval_trace else []
            context["source_type_merge_results"] = merged
            context["retrieval_per_question"] = [
                {
                    "question_id": trace["question_id"],
                    "query": trace["query"],
                    "source": trace["source"],
                    "result_count": trace["result_count"],
                    "retrieved_chunk_ids": [str(chunk.get("id", "")) for chunk in trace["source_type_merge_results"]],
                }
                for trace in retrieval_trace
            ]
            context["retrieval_strategy"] = strategy
            merged = expand_neighbor_chunks(merged, conn)
            merged = expand_parent_sections(merged, conn)
            context["context_expansions"] = [
                chunk.get("expansion_trace")
                for chunk in merged
                if chunk.get("expansion_trace")
            ][:20]
        finally:
            release_db_connection(conn)

        if merged:
            save_cached_chunks(cache_key, merged, query_text=search_query, chunk_count=len(merged))

        context["retrieved_chunks"] = merged
        context["retrieval_cache_hit"] = False
        return context

    except Exception as e:
        logger.error(f"Retriever failed — returning empty results: {e}", exc_info=True)
        context["retrieved_chunks"] = []
        context["retrieval_cache_hit"] = False
        return context
