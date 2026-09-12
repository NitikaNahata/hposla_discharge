# Discharge Planning Copilot — Production Image
# Build: docker build -t discharge-copilot:latest .
# Run:   docker run --env-file .env discharge-copilot:latest

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

RUN git config --global user.email "nitikaj025@gmail.com" && \
    git config --global user.name "Discharge Copilot CI"

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Install the package
RUN pip install --no-cache-dir -e .

# Create directories for runtime state
RUN mkdir -p /app/.state /app/evidence

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "from discharge_copilot.config import get_config; get_config()" || exit 1

# Default command — runs the CLI
ENTRYPOINT ["python", "-m", "discharge_copilot"]
CMD ["run", "--case", "data/samples/case_001.json"]
