# ---------------------------------------------------------------------------
# Undertow Engine – Dockerfile
# Python 3.11 slim + ffmpeg + Playwright OS dependencies
# ---------------------------------------------------------------------------

FROM python:3.11-slim AS base

# --- System dependencies ---------------------------------------------------
# ffmpeg          : video compositing (libass burns the ASS captions) + pydub audio
# fonts-montserrat: caption typeface, resolved by libass via fontconfig
# Playwright      : Chromium headless OS-level libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
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
        fonts-montserrat \
        wget \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Application directory -------------------------------------------------
WORKDIR /app

# --- Python dependencies ---------------------------------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium to a shared, fixed path (not root's HOME cache),
# so the non-root undertow user finds it at runtime via the same env var.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium

# --- Copy application source -----------------------------------------------
COPY . .

# --- Non-root user for security --------------------------------------------
# Give undertow a real home (fontconfig/libass cache lives under XDG_CACHE_HOME)
# and own the data dirs the compose volumes mount over. A freshly-created named
# volume inherits the ownership of the image directory it shadows, so creating
# these as undertow here makes /data/outputs and the Chromium profile writable
# without running the app as root.
RUN groupadd -r undertow \
    && useradd -r -g undertow -m -d /home/undertow undertow \
    && mkdir -p /data/outputs /data/chromium-profile /home/undertow/.cache \
    && chown -R undertow:undertow /app /data /home/undertow /ms-playwright
ENV HOME=/home/undertow \
    XDG_CACHE_HOME=/home/undertow/.cache
USER undertow

# --- Default command (overridden per service in docker-compose) ------------
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
