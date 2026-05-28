# syntax=docker/dockerfile:1.6

# ---------- Builder ----------
FROM node:20-alpine AS builder
WORKDIR /app

# Corporate CA trust — must precede any HTTPS network call (apk/npm).
# certs/ may be empty for non-corporate builds; the loop is a no-op then.
COPY certs/ /usr/local/share/ca-certificates/
RUN for f in /usr/local/share/ca-certificates/*.crt; do \
      if [ -f "$f" ]; then cat "$f" >> /etc/ssl/certs/ca-certificates.crt; fi; \
    done
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

RUN apk add --no-cache libc6-compat

COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------- Runner ----------
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
 && adduser  --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000
CMD ["node", "server.js"]
