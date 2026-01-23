#!/bin/bash

remote_result() {
  REMOTE_NAME="result"
  REMOTE_URL="git@github.com:Team-Atlanta/multilang-llm-agent-result.git"

  if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    echo "✅ Remote '$REMOTE_NAME' already exists."
  else
    echo "🚀 Remote '$REMOTE_NAME' not found. Adding it now..."
    git remote add "$REMOTE_NAME" "$REMOTE_URL"
    echo "✅ Remote '$REMOTE_NAME' added successfully."
  fi
}
CHANGED_FILES=$(git diff --cached --name-only)
GIT_ROOT=$(git rev-parse --show-toplevel)

if echo "$CHANGED_FILES" | grep -q "^results/"; then
  remote_result
  echo "⚠️  results/ is modified. Executing push_result.sh..."
  bash $GIT_ROOT/push_result.sh
else
  echo "✅ results/ is unmodified."
fi
