FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# geopandas/pyogrio/shapely/psycopg[binary] ship manylinux wheels with their
# native libs bundled -- no apt build/runtime deps needed on slim.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd -m app && chown -R app /app
USER app

EXPOSE 8000

# Render provides $PORT. WEB_CONCURRENCY tunes workers (caches are per-worker).
CMD ["sh", "-c", "gunicorn main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout 120 --access-logfile - --error-logfile -"]
