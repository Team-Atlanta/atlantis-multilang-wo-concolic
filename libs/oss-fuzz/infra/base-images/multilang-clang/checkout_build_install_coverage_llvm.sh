#!/bin/bash -eux

NPROC=$(nproc)

TARGET_TO_BUILD=
case $(uname -m) in
    x86_64)
      TARGET_TO_BUILD=X86
      ARCHITECTURE_DEPS="g++-multilib"
      # Use chromium's clang revision.
      export CC=$WORK/llvm-stage1/bin/clang
      export CXX=$WORK/llvm-stage1/bin/clang++
      ;;
    aarch64)
      TARGET_TO_BUILD=AArch64
      # g++ multilib is not needed on AArch64 because we don't care about i386.
      # We need to install clang and lld using apt because the binary downloaded
      # from Chrome's developer tools doesn't support AArch64.
      # TODO(metzman): Make x86_64 use the distro's clang for consistency once
      # we support AArch64 fully.
      ARCHITECTURE_DEPS="clang lld g++"
      export CC=clang
      export CXX=clang++
      ;;
    *)
      echo "Error: unsupported target $(uname -m)"
      exit 1
      ;;
esac

INTROSPECTOR_DEP_PACKAGES="texinfo bison flex"
# zlib1g-dev is needed for llvm-profdata to handle coverage data from rust compiler
LLVM_DEP_PACKAGES="build-essential make ninja-build git python3 python3-distutils binutils-dev zlib1g-dev $ARCHITECTURE_DEPS $INTROSPECTOR_DEP_PACKAGES"

apt-get update && apt-get install -y $LLVM_DEP_PACKAGES --no-install-recommends

git clone --depth 1 --branch llvmorg-18.1.8 https://github.com/llvm/llvm-project.git /llvm-project-coverage
cd /llvm-project-coverage

git switch -c llvm-cov-custom

rm -rf /llvm-project-coverage/build /llvm-project-coverage/install

cp /root/sanitizer_on_print.diff /llvm-project-coverage/sanitizer_on_print.diff
git apply sanitizer_on_print.diff

# Build directory
mkdir -p /llvm-project-coverage/build
cd /llvm-project-coverage/build

# Configure with CMake
cmake -G Ninja ../llvm \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLVM_ENABLE_PROJECTS="clang;lld" \
  -DLLVM_ENABLE_RUNTIMES="compiler-rt;libcxx;libcxxabi" \
  -DLLVM_BINUTILS_INCDIR="/usr/include/" \
  -DLLVM_TARGETS_TO_BUILD="X86" \
  -DCMAKE_INSTALL_PREFIX=/opt/llvm-patched \
  -DLIBCXXABI_USE_LLVM_UNWINDER=OFF \
  -DLIBCXX_ENABLE_SHARED=OFF \
  -DLIBCXX_ENABLE_STATIC_ABI_LIBRARY=ON \
  -DLIBCXXABI_ENABLE_SHARED=OFF \
  -DCMAKE_C_COMPILER=clang \
  -DCMAKE_CXX_COMPILER=clang++

ninja
ninja install

apt-get autoremove --purge -y $LLVM_DEP_PACKAGES