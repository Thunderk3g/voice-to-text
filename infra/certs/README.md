# Corporate CA bundle

Drop any organisation root / intermediate CA certificates here in PEM format
with a `.crt` extension (e.g. `bajaj-root-ca.crt`). All `*.crt` files are baked
into every backend image at build time and trusted by `apt`, `pip`, `curl`,
`requests`, and the OpenSSL stack inside the container.

This is needed when an SSL inspection proxy (Zscaler / Netskope / etc.) sits
between the build host and the internet — the proxy re-signs HTTPS with a
corporate CA that is not in the distro's default trust store, so `apt-get
update` and `pip install` fail with `certificate verify failed` inside the
container.

The frontend image has a parallel copy at `frontend/certs/` (its build context
is `./frontend` and cannot reach this directory). Use
`scripts/install-corp-ca.ps1 <source.crt>` to populate both locations from a
single source file.

Files in this directory other than `.gitkeep` and this README are gitignored
by default — corporate CA certificates are not secrets, but committing one
ties the repo to a specific organisation.
