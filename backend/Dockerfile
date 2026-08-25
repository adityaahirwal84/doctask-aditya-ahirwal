FROM python:3.12-slim

# libgl1/libglib2.0-0: PyMuPDF's font/image rendering deps. Nothing else -
# this image has no OCR toolchain, no Redis/Celery client libs, because
# the running system doesn't use them (see docs/architecture.md).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
