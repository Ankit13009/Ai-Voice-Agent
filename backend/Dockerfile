# Python 3.12: 3.13 removed several stdlib modules some dependencies still use,
# and there is nothing here that needs a newer runtime.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Dependencies first, so a code change does not invalidate the pip layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Fly injects PORT. The default matches the internal_port in fly.toml.
ENV PORT=8080
# Migrate, then serve. Running migrations at container start keeps schema and
# code deploying together; a schema change that fails stops the deploy instead
# of leaving the new code running against the old tables.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
