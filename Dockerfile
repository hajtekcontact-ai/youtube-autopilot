# YouTube AI Autopilot - Production Container Image
# Multi-stage optimized build for Python 3.12 with FFmpeg and CJK/Arabic typography

FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered streaming logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    APP_ENV=production \
    PORT=5000 \
    TIMEZONE=America/New_York

# Install system dependencies: FFmpeg, required font families, and diagnostic curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-noto \
    fonts-liberation \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root system user and group for security compliance
RUN groupadd -g 1000 autopilot && \
    useradd -u 1000 -g autopilot -m -s /bin/bash autopilot

# Establish application working directory
WORKDIR /app

# Cache and install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /app/requirements.txt

# Copy application source code and assets with proper ownership
COPY --chown=autopilot:autopilot . /app

# Ensure runtime directories exist with appropriate write permissions
RUN mkdir -p /app/data /app/data/logs /app/data/temp /app/output /app/assets/thumbnails && \
    chown -R autopilot:autopilot /app/data /app/output /app/assets

# Expose internal dashboard listening port
EXPOSE 5000

# Container health inspection
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5000/api/health || python main.py health || exit 1

# Drop privileges to non-root user
USER autopilot

# Default execution entrypoint: start web dashboard
CMD ["python", "main.py", "serve"]
