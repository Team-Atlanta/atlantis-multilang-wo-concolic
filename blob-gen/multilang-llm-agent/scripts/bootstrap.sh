#!/usr/bin/env bash
set -e

IS_CI=false
WORKSPACE=""

for arg in "$@"; do
  case $arg in
    --ci)
      IS_CI=true
      ;;
    /*)
      WORKSPACE="$arg"
      ;;
    *)
      echo "❌ Unknown argument: $arg"
      exit 1
      ;;
  esac
done

echo "🔵 Starting environment setup for Multilang LLM Agent (MLLA)... (CI: $IS_CI, WORKSPACE: $WORKSPACE)"

# 0. Install system dependencies (Ubuntu/Debian) — only if not in CI
if [ "$IS_CI" = "false" ] && command -v apt-get &> /dev/null; then
  echo "🔵 Installing system dependencies..."
  sudo apt-get update
  sudo apt-get install -y \
    build-essential zlib1g-dev libffi-dev libssl-dev libbz2-dev \
    libreadline-dev libsqlite3-dev liblzma-dev libncurses-dev tk-dev ripgrep curl git
else
  echo "⚠️  Skipping system dependency installation (CI mode or non-Debian system)."
fi

# 1. Install Python 3.11.8 using pyenv
if ! command -v pyenv &> /dev/null; then
  echo "🔵 Installing pyenv for Python version management..."
  curl https://pyenv.run | bash

  export PATH="$HOME/.pyenv/bin:$PATH"
  export PATH="$HOME/.pyenv/shims:$PATH"
  eval "$(pyenv init --path)"
  eval "$(pyenv init -)"
  eval "$(pyenv virtualenv-init -)"
fi

echo "🔵 Checking for Python 3.11.8..."
if ! pyenv versions | grep -q "3.11.8"; then
  echo "🔵 Installing Python 3.11.8 via pyenv..."
  pyenv install 3.11.8
fi

# Set local python version only if not already set
CURRENT_PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
if [[ "$CURRENT_PYTHON_VERSION" != "3.11.8" ]]; then
  echo "🔵 Setting local Python version to 3.11.8..."
  pyenv local 3.11.8
fi

# 2. Install Rust (if not installed)
if ! command -v cargo &> /dev/null; then
  echo "🔵 Installing Rust via rustup..."
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  source "$HOME/.cargo/env"
fi

# 3. Install pip packages needed globally
echo "🔵 Checking for Poetry, Maturin, and Pre-commit..."
NEEDS_INSTALL=false
for package in poetry maturin pre-commit; do
  if ! pip show "$package" &> /dev/null; then
    NEEDS_INSTALL=true
    break
  fi
done

if [ "$NEEDS_INSTALL" = true ]; then
  echo "🔵 Installing Python tools: poetry, maturin, pre-commit..."
  pip install --upgrade pip
  pip install poetry maturin pre-commit
else
  echo "🟢 Poetry, Maturin, and Pre-commit already installed. Skipping."
fi

# 4. Clone CRS-multilang if it does not exist
CRS_MULTILANG_DIR=${CRS_MULTILANG:-"./CRS-multilang"}
if [ ! -d "$CRS_MULTILANG_DIR" ]; then
  echo "🔵 Cloning CRS-multilang repository..."
  git clone --recurse-submodules git@github.com:Team-Atlanta/CRS-multilang.git $CRS_MULTILANG_DIR
else
  echo "🟡 CRS-multilang already exists at $CRS_MULTILANG_DIR. Pulling latest changes..."
  pushd $CRS_MULTILANG_DIR
  git pull origin main
  git submodule update --init --recursive
  popd
fi

# 4-1 (CI) overwrite current module
if [ "$IS_CI" = "true" ]; then
  echo "🔁 CI detected: Overwriting multilang-llm-agent in CRS-multilang repo with local working copy..."
  pushd $CRS_MULTILANG_DIR
  rm -rf "$CRS_MULTILANG_DIR/blob-gen/multilang-llm-agent"
  rsync -a --delete \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='results' \
    "$WORKSPACE/" "$CRS_MULTILANG_DIR/blob-gen/multilang-llm-agent/"
  popd
fi

# 5. Install Python dependencies for CRS-multilang
echo "🔵 Installing CRS-multilang Python dependencies..."
pushd $CRS_MULTILANG_DIR
pip install -r requirements.txt
pip install pyyaml coloredlogs
popd

# 6. Install all Poetry dependencies for MLLA first
echo "🔵 Installing MLLA dependencies using Poetry..."
poetry install --with test --with telemetry

# 7. Activate virtualenv manually
echo "🔵 Activating Poetry virtualenv..."
VENV_PATH=$(poetry env info --path)
source "$VENV_PATH/bin/activate"

# 8. Build libfdp
echo "🔵 Building libFDP with their script..."
pushd $CRS_MULTILANG_DIR/libs/libFDP
./build_pymodule.sh
popd

# 9. Install pre-commit hooks
echo "🔵 Installing pre-commit hooks..."
pre-commit install --hook-type pre-commit --hook-type pre-push

# 10. Done
echo "✅ Environment setup complete! You are ready to develop."
