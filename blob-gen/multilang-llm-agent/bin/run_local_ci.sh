#!/usr/bin/env bash

set -e

# Environment variables from default-checker.yaml
export GITHUB_ACTIONS="true"
export CODE_INDEXER_REDIS_HOST="localhost"
export CODE_INDEXER_REDIS_PORT="32321"
export CODE_INDEXER_REDIS_URL="localhost:32321"
export CI_REDIS_CONTAINER_NAME="ci-redis-test-local"

# Cleanup existing Redis container if it exists
if docker ps -a --format '{{.Names}}' | grep -q "^${CI_REDIS_CONTAINER_NAME}$"; then
  docker rm -f "${CI_REDIS_CONTAINER_NAME}"
fi

# Start Redis with custom port
echo "Starting Redis container..."
docker run -d --name "${CI_REDIS_CONTAINER_NAME}" -p "${CODE_INDEXER_REDIS_PORT}:6379" redis:latest

# Wait for Redis to be ready
echo "Waiting for Redis to be ready..."
until docker exec "${CI_REDIS_CONTAINER_NAME}" redis-cli ping &>/dev/null; do
  sleep 1
done

echo "Redis is ready"

# Run tests
echo "Running tests..."
poetry run pytest -s -v tests/ --ignore=tests/test_summarize.py

# Cleanup
echo "Cleaning up..."
docker rm -f "${CI_REDIS_CONTAINER_NAME}"

# Unset environment variables
unset CODE_INDEXER_REDIS_HOST
unset CODE_INDEXER_REDIS_PORT
unset CODE_INDEXER_REDIS_URL
unset CI_REDIS_CONTAINER_NAME
