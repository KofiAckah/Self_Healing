# TechStream API image. Pinned base, non-root runtime user.
FROM python:3.12-slim

# Don't write .pyc files; flush stdout/stderr immediately for clean logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 5000

# Chaos state lives in-process, so we run a SINGLE worker with threads. Multiple
# workers would each hold their own chaos dict and the demo would be incoherent.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", \
     "--timeout", "120", "app:app"]
