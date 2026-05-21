-- List app schema tables
SELECT table_name
FROM information_schema.tables
WHERE table_schema IN ('knowledge', 'ops')
ORDER BY table_name;

-- Latest feedback
SELECT *
FROM feedback
ORDER BY created_at DESC
LIMIT 20;

-- Latest API calls
SELECT *
FROM api_calls
ORDER BY created_at DESC
LIMIT 20;

-- Cache tables
SELECT *
FROM response_cache
ORDER BY created_at DESC
LIMIT 20;

SELECT *
FROM retrieval_cache
ORDER BY created_at DESC
LIMIT 20;

-- Latest run traces
SELECT trace_id, created_at, product, platform, role, redacted_ticket_preview
FROM run_trace
ORDER BY created_at DESC
LIMIT 20;

-- Row counts per table
SELECT 'knowledge_base' AS table_name, COUNT(*) AS row_count FROM knowledge.knowledge_base
UNION ALL
SELECT 'response_cache', COUNT(*) FROM ops.response_cache
UNION ALL
SELECT 'retrieval_cache', COUNT(*) FROM ops.retrieval_cache
UNION ALL
SELECT 'feedback', COUNT(*) FROM ops.feedback
UNION ALL
SELECT 'api_calls', COUNT(*) FROM ops.api_calls
UNION ALL
SELECT 'run_trace', COUNT(*) FROM ops.run_trace;
