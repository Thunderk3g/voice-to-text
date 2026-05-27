# =========================================================================
# v2t Postgres image — pgvector/pgvector:pg16 + init scripts
# Extensions: vector (pgvector), pgcrypto (gen_random_uuid())
# =========================================================================
FROM pgvector/pgvector:pg16

LABEL org.opencontainers.image.title="v2t-postgres" \
      org.opencontainers.image.description="Postgres 16 + pgvector + pgcrypto init for v2t" \
      org.opencontainers.image.source="https://github.com/your-org/v2t"

# Postgres runs *.sql / *.sh from this dir exactly once on a fresh data volume.
COPY init.sql /docker-entrypoint-initdb.d/00-init.sql

# Default healthcheck-friendly settings; compose layer adds pg_isready CMD.
EXPOSE 5432
