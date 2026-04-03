# ---------------------------------------------------------------------------
# Undertow Engine – Dockerfile
# Python 3.11 slim + ffmpeg + ImageMagick + Playwright OS dependencies
# ---------------------------------------------------------------------------

FROM python:3.11-slim AS base

# --- System dependencies ---------------------------------------------------
# ffmpeg       : required by MoviePy / pydub for audio/video processing
# imagemagick  : required by MoviePy for TextClip rendering
# Playwright   : Chromium headless OS-level libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        imagemagick \
        # Playwright / Chromium headless dependencies
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libdbus-1-3 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libasound2 \
        libpango-1.0-0 \
        libcairo2 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxext6 \
        fonts-open-sans \
        wget \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- ImageMagick policy: allow MoviePy to use the full policy set ----------
# The default Debian policy blocks many operations needed by MoviePy.
RUN sed -i 's/<policy domain="path" rights="none" pattern="@\*"\/>/<!-- &-->/' \
        /etc/ImageMagick-6/policy.xml || true

# --- Application directory -------------------------------------------------
WORKDIR /app

# --- Python dependencies ---------------------------------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser
RUN playwright install chromium --with-deps

# --- Copy application source -----------------------------------------------
COPY . .

# --- Non-root user for security --------------------------------------------
RUN groupadd -r undertow && useradd -r -g undertow undertow \
    && chown -R undertow:undertow /app
USER undertow

# --- Default command (overridden per service in docker-compose) ------------
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
