#!/usr/bin/env bash
set -e

echo "Building frontend..."
cd frontend
npm install --prefer-offline
npm run build
echo "Frontend build complete."
