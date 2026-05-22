FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# shapely/psycopg[binary] ship manylinux wheels with their native libs
# bundled -- no apt build/runtime deps needed on slim. (geopandas is a
# build-only dep now; the app uses shapely directly to stay lean.)
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd -m app && chown -R app /app
USER app

EXPOSE 8000

# Render provides $PORT. WEB_CONCURRENCY tunes workers; default 1. Now that
# geopandas is gone the per-worker baseline is ~30MB (was ~106MB), so the
# free tier has room to raise this if needed.
CMD ["sh", "-c", "gunicorn main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout 120 --access-logfile - --error-logfile -"]
