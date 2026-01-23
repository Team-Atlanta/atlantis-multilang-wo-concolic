#!/bin/bash

set -e
set -x

parent_commit=$(git rev-parse HEAD)
repo_url="https://github.com/Team-Atlanta/multilang-llm-agent"
commit_url="$repo_url/commit/$parent_commit"

sudo chown -R $USER:$USER results
cd results
git add .
if git diff-index --quiet HEAD --; then
    echo "No changes to commit"
else
    if [ -z "$COMMIT_MSG" ]; then
        commit_message="Update results (parent commit: $commit_url)"
    else
        commit_message="$COMMIT_MSG"
    fi
    git commit -m "$commit_message"

    if [ -z "$GH_PAT" ]; then
        echo "GH_PAT is not set."
        git push origin main
        exit 0
    fi

    REMOTE_URL=$(git remote get-url origin)
    # change HTTPS URL
    if [[ $REMOTE_URL == git@github.com:* ]]; then
    # SSH URL to HTTPS URL
        REPO_PATH=${REMOTE_URL#git@github.com:}
        REPO_PATH=${REPO_PATH%.git}
        HTTPS_URL="https://${GH_PAT}@github.com/${REPO_PATH}.git"
    elif [[ $REMOTE_URL == https://github.com/* ]]; then
        # add token to existing HTTPS URL
        REPO_PATH=${REMOTE_URL#https://github.com/}
        REPO_PATH=${REPO_PATH%.git}
        HTTPS_URL="https://${GH_PAT}@github.com/${REPO_PATH}.git"
    else
        echo "Unsupported remote repository URL format: $REMOTE_URL"
        git push origin main
        exit 1
    fi

    git push $HTTPS_URL main
fi
cd ..
