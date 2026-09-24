# SpillTrace — single-service container for Hugging Face Spaces (Docker SDK).
#
# The whole product is one stdlib Python web server (scripts/run_api.py) that serves
# BOTH the JSON API and the frontend, with no database. The trained model, the preview
# PNGs and the demo case files are all committed to git, so this image is self-contained:
# it needs no raw dataset and makes no network calls at runtime.
#
# Python 3.11-slim matches the tested interpreter (3.11.15 / numpy 2.4.4 / opencv 4.13).

FROM python:3.11-slim

# opencv-python links against libGL and glib at import time. On a slim image those
# system libraries are absent, so `import cv2` fails with a bare ImportError until
# they are installed. (Alternative: swap opencv-python for opencv-python-headless in
# requirements.txt and drop this apt-get line — but this keeps the tested wheel.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the tracked tree (see .dockerignore for what stays out).
COPY . .

# Hugging Face Spaces routes to port 7860 by default.
EXPOSE 7860

# Bind all interfaces, and take the port from $PORT when the platform assigns one
# (Render, Cloud Run and friends inject it), falling back to 7860 otherwise. Shell form
# so ${PORT:-7860} is expanded. --no-demo: the case JSONs are already committed on disk,
# so there is nothing to build at boot and no raw scenes to look for.
CMD python scripts/run_api.py --host 0.0.0.0 --port ${PORT:-7860} --no-demo
