# Build static assets once on the builder architecture. The Caddy binary
# selects the target architecture without emulating a Node installation.
FROM caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS caddy-runtime
FROM --platform=$BUILDPLATFORM node:24.20.0-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e AS build
WORKDIR /app
ENV ASTRO_TELEMETRY_DISABLED=1
COPY package.json package-lock.json .npmrc ./
RUN npm ci --ignore-scripts
COPY astro.config.mjs tsconfig.json ./
COPY src/ ./src/
COPY design/ ./design/
COPY content/ ./content/
COPY deploy/reserved-paths.json ./deploy/reserved-paths.json
COPY tests/ ./tests/
RUN npm run verify
# Copy the target binary as data on the native builder. Plain cp drops the
# upstream file capability (cap_net_bind_service), unnecessary on port 8080.
RUN --mount=from=caddy-runtime,source=/usr/bin/caddy,target=/tmp/caddy \
    cp /tmp/caddy /app/caddy

FROM alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40
COPY --from=build /app/caddy /usr/local/bin/caddy
COPY docker/caddy.json /etc/caddy/caddy.json
COPY --from=build /app/dist/ /srv/
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/_health || exit 1
CMD ["/usr/local/bin/caddy", "run", "--config", "/etc/caddy/caddy.json"]
