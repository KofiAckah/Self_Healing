# Remediation service image. Talks to the Docker socket PROXY (not the raw
# socket), so it can run as a non-root user with no special host privileges.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY webhook_handler.py .

RUN useradd --create-home --uid 10002 remediator
USER remediator

EXPOSE 8081

CMD ["gunicorn", "--bind", "0.0.0.0:8081", "--workers", "1", "--threads", "4", \
     "--timeout", "60", "webhook_handler:app"]
