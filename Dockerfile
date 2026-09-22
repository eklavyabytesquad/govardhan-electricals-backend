# Pure Python standard library — no extra packages to install.
FROM python:3.12-slim

WORKDIR /app
COPY . .

# Run as a non-root user
RUN useradd -m appuser
USER appuser

ENV HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["python", "server.py"]
