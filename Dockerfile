FROM caddy:2 AS proxy
FROM python:3.12-slim
COPY --from=proxy /usr/bin/caddy /usr/bin/caddy
WORKDIR /srv/birga
COPY app ./app
COPY web ./web
COPY deploy ./deploy
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 APP_MODE=telegram DATABASE_PATH=/data/birga.sqlite3
CMD ["python", "-m", "app.hosted"]
