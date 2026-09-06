# Veritas — Linux Build Container
# =================================
# Builds veritas-runtime (Linux binary) using PyInstaller.
# Run this from any machine (Windows/Mac/Linux) to produce a Linux binary.
#
# Usage:
#   docker build -f build-linux.Dockerfile -t veritas-builder .
#   docker run --name veritas-build veritas-builder
#   docker cp veritas-build:/build/dist/veritas-runtime ./dist/linux/veritas-runtime
#   docker rm veritas-build

FROM python:3.12-slim

WORKDIR /build

# Install system deps
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    gcc g++ libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY . .

# Install Python deps + PyInstaller
RUN pip install --upgrade pip --quiet && \
    pip install pyinstaller --quiet && \
    pip install -r requirements.txt --quiet

# Download spaCy model
RUN python -m spacy download en_core_web_lg --quiet

# Build
RUN python -m PyInstaller veritas-linux.spec --noconfirm

# The binary is at /build/dist/veritas-runtime
CMD ["ls", "-lh", "dist/"]
