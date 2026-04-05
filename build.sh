#!/usr/bin/env bash
# -------------------------------------------------------
# ABS Rename — Docker build script
# Run from the project root: bash build.sh
# -------------------------------------------------------

set -euo pipefail

IMAGE_NAME="abs-rename"
IMAGE_TAG="latest"

echo "==> Building Docker image: ${IMAGE_NAME}:${IMAGE_TAG}"
docker build \
  --tag "${IMAGE_NAME}:${IMAGE_TAG}" \
  --file Dockerfile \
  .

echo ""
echo "==> Build complete."
echo "    Image : ${IMAGE_NAME}:${IMAGE_TAG}"
echo ""
echo "==> To run locally for testing:"
echo "    docker run -d \\"
echo "      -p 8000:8000 \\"
echo "      -v /mnt/user/appdata/abs-rename/data:/data \\"
echo "      -v /mnt/user/audiobooks:/audiobooks \\"
echo "      -v /mnt/user/audiobooks-organized:/output \\"
echo "      --name abs-rename \\"
echo "      ${IMAGE_NAME}:${IMAGE_TAG}"
