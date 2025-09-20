# ---- Builder Stage ----
FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc \
    libpq-dev libffi-dev libssl-dev \
    proj-bin proj-data libproj-dev \
    libgeos-dev \
    rustc cargo \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Copy only requirements first (better cache)
COPY requirements.txt .

# Build wheels for dependencies
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- Final Runtime Stage ----
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libffi8 libssl3 \
    proj-bin proj-data \
    libgeos-c1v5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Install deps from built wheels
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*

# Copy only runtime source code
COPY ./app /code/app

# Set workdir inside app
WORKDIR /code/app

EXPOSE 8080
ENV PYTHONPATH=/code

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
