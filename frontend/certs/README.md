# Corporate CA bundle (frontend)

Parallel copy of `infra/certs/` for the Next.js image — the frontend's docker
build context is `./frontend` so it cannot reach `../infra/certs`. Keep the
same `*.crt` files in both directories. See `infra/certs/README.md` for the
full explanation; use `scripts/install-corp-ca.ps1 <source.crt>` to populate
both locations from one source file.
