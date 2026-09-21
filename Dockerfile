FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies needed for Playwright and Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium with OS dependencies
RUN playwright install --with-deps chromium

# Copy project files
COPY . .

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV HEADLESS=True
ENV PORT=5000

# Expose port
EXPOSE 5000

# Start Flask app via Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "0", "app:app"]
