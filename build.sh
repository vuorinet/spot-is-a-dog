#!/bin/bash
# Build script that automatically sets version from git SHA
set -e

# Detect docker compose command
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DOCKER_COMPOSE="docker-compose"
else
    echo "❌ Error: Neither 'docker compose' nor 'docker-compose' found"
    exit 1
fi

# Get git SHA (short form) or use timestamp if not in a git repo
if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
    GIT_SHA=$(git rev-parse --short HEAD)
    GIT_DIRTY=""
    
    # Check if there are uncommitted changes
    if ! git diff-index --quiet HEAD --; then
        GIT_DIRTY="-dirty"
        echo "⚠️  Warning: Working directory has uncommitted changes"
    fi
    
    VERSION="${GIT_SHA}${GIT_DIRTY}"
    echo "📝 Building with git version: ${VERSION}"
else
    VERSION=$(date +%Y%m%d-%H%M%S)
    echo "⚠️  Not in a git repository, using timestamp: ${VERSION}"
fi

# Export version for docker-compose (build args are passed via compose)
export SPOT_VERSION="${VERSION}"

# Build
echo "🔨 Building Docker image..."
${DOCKER_COMPOSE} build

echo "✅ Build complete! Version: ${VERSION}"
echo ""
echo "To start the application:"
echo "  ${DOCKER_COMPOSE} up -d"
echo ""
echo "To verify version in image:"
echo "  ${DOCKER_COMPOSE} run --rm app env | grep SPOT_VERSION"

