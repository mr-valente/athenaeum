FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000 DB_PATH=/data/app.db
COPY accounts/requirements.lock ./
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.lock
COPY accounts/ ./accounts/
COPY design/tokens.css design/base.css design/mark.svg ./accounts/static/
COPY design/fonts/ ./accounts/static/fonts/
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/_health',timeout=2).read()"
ARG ATHENAEUM_VERSION=dev
LABEL org.opencontainers.image.version=$ATHENAEUM_VERSION
CMD ["python", "-m", "accounts"]
