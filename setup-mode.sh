#!/bin/bash
# Switch between host docker and DinD modes
#
# Usage:
#   ./setup-mode.sh host   # Use host docker builder (default)
#   ./setup-mode.sh dind   # Use Docker-in-Docker mode
#   ./setup-mode.sh        # Show current mode
#
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

show_current_mode() {
    if [ -L builder.Dockerfile ]; then
        target=$(readlink builder.Dockerfile)
        case "$target" in
            *oss-crs-dind*)
                echo "Current mode: dind"
                ;;
            *oss-crs/*)
                echo "Current mode: host"
                ;;
            *)
                echo "Current mode: unknown (symlink points to: $target)"
                ;;
        esac
    else
        echo "Current mode: unknown (builder.Dockerfile is not a symlink)"
    fi
}

set_mode() {
    local mode="$1"

    case "$mode" in
        host)
            rm -f builder.Dockerfile runner.Dockerfile
            ln -s oss-crs/builder.Dockerfile builder.Dockerfile
            ln -s oss-crs/runner.Dockerfile runner.Dockerfile
            echo "Switched to host mode"
            echo "  builder.Dockerfile -> oss-crs/builder.Dockerfile"
            echo "  runner.Dockerfile  -> oss-crs/runner.Dockerfile"
            ;;
        dind)
            rm -f builder.Dockerfile runner.Dockerfile
            ln -s oss-crs-dind/builder.Dockerfile builder.Dockerfile
            ln -s oss-crs-dind/runner.Dockerfile runner.Dockerfile
            echo "Switched to dind mode"
            echo "  builder.Dockerfile -> oss-crs-dind/builder.Dockerfile"
            echo "  runner.Dockerfile  -> oss-crs-dind/runner.Dockerfile"
            ;;
        *)
            echo "Usage: $0 [host|dind]"
            echo ""
            echo "Modes:"
            echo "  host - Use host docker builder (default, for oss-crs)"
            echo "  dind - Use Docker-in-Docker mode"
            echo ""
            show_current_mode
            exit 1
            ;;
    esac
}

if [ $# -eq 0 ]; then
    show_current_mode
else
    set_mode "$1"
fi
