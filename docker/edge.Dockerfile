FROM caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648 AS upstream
FROM --platform=$BUILDPLATFORM alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40 AS binary
# Native cp drops the upstream low-port capability without executing target code.
RUN --mount=from=upstream,source=/usr/bin/caddy,target=/tmp/caddy cp /tmp/caddy /caddy
FROM alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40
COPY --from=binary /caddy /usr/local/bin/caddy
COPY --from=upstream /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY deploy/caddy/ /etc/caddy/
ENV XDG_DATA_HOME=/data XDG_CONFIG_HOME=/config
USER 10001:10001
EXPOSE 8080 8443
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8081/_health || exit 1
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
