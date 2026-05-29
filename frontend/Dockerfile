# syntax=docker/dockerfile:1.6

# ---------- Builder ----------
# Debian-based Node image (instead of alpine) because the corporate MITM
# proxy corrupts the .apk payloads from dl-cdn.alpinelinux.org. Debian's
# deb.debian.org mirror uses plain HTTP and passes through cleanly.
FROM node:20-slim AS builder
WORKDIR /app

# Install ca-certificates first using plain HTTP debian mirrors
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Corporate CA trust — must precede any HTTPS network call (npm/Node).
# certs/ may be empty for non-corporate builds; update-ca-certificates is a no-op then.
COPY certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .

# Compose passes NEXT_PUBLIC_API_BASE_URL at runtime, but Next.js bakes
# rewrite destinations and any NEXT_PUBLIC_* values at *build* time. Accept
# it as a build-arg so the URL the standalone server proxies to is correct.
ARG NEXT_PUBLIC_API_BASE_URL=http://api:8080
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------- Runner ----------
FROM node:20-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Install ca-certificates in the runner stage too, for outbound calls
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY certs/ /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

RUN groupadd --system --gid 1001 nodejs \
 && useradd  --system --uid 1001 --gid 1001 nextjs

# Create empty public dir if the project doesn't have one (Next.js still
# expects the directory at runtime when standalone output is enabled).
RUN mkdir -p public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000
CMD ["node", "server.js"]
