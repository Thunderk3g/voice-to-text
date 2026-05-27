-- =========================================================================
-- v2t Postgres bootstrap
-- Runs once on first cluster init (empty data volume) via
-- /docker-entrypoint-initdb.d hook.
--
-- Required extensions:
--   * vector    -> pgvector(1024) columns for embeddings + centroids
--   * pgcrypto  -> gen_random_uuid() default for all UUID primary keys
-- =========================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Lightweight smoke-check that surfaces in `docker compose logs postgres`.
DO $$
BEGIN
    RAISE NOTICE 'v2t init.sql: vector=%, pgcrypto=%',
        (SELECT extversion FROM pg_extension WHERE extname = 'vector'),
        (SELECT extversion FROM pg_extension WHERE extname = 'pgcrypto');
END$$;
