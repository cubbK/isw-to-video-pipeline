# ---- Build stage ----
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim

# Non-root user
RUN useradd --create-home appuser

WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .

USER appuser

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
