# -------------------------------------------------------
# ABS Rename — Dockerfile
# -------------------------------------------------------
# Build:  docker build -t abs-rename .
# Run:    docker run -p 8000:8000 \
#           -v /path/to/audiobooks:/audiobooks \
#           -v /path/to/output:/output \
#           -v /path/to/data:/data \
#           abs-rename
# -------------------------------------------------------

FROM python:3.12-slim

# Set working directory inside the container
WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Create a directory for the SQLite database inside the container.
# Map this to a host volume so data persists between restarts.
RUN mkdir -p /data
ENV DATABASE_PATH=/data/abs_rename.db

# Default environment values (override with -e or --env-file)
ENV HOST=0.0.0.0
ENV PORT=8000
ENV DEBUG=false
ENV AUDNEXUS_REGION=us
ENV AUDNEXUS_REQUEST_DELAY_MS=400

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host $HOST --port $PORT"]
