#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <dst-path>" >&2
  exit 1
fi

dst_path="$1"
clang_lib_root="${CLANG_LIB_ROOT:-/usr/local/lib/clang}"
clang_bin="${CLANG_BIN:-clang}"
target_triple_dir="${TARGET_TRIPLE_DIR:-$(uname -m)-unknown-linux-gnu}"

candidate=""
if command -v "$clang_bin" >/dev/null 2>&1; then
  resource_dir="$("$clang_bin" --print-resource-dir)"
  active_candidate="${resource_dir}/lib/${target_triple_dir}/libclang_rt.fuzzer.a"
  if [ -f "$active_candidate" ]; then
    candidate="$active_candidate"
  fi
fi

if [ -z "$candidate" ]; then
  matches="$(
    find "$clang_lib_root" -path "*/lib/${target_triple_dir}/libclang_rt.fuzzer.a" \
      | sort
  )"
  match_count="$(
    printf '%s\n' "$matches" | sed '/^$/d' | wc -l | tr -d ' '
  )"

  if [ "$match_count" -gt 1 ]; then
    echo "multiple libclang_rt.fuzzer.a candidates found under ${clang_lib_root}" >&2
    exit 1
  fi

  candidate="$(printf '%s\n' "$matches" | sed -n '1p')"
fi

if [ -z "$candidate" ] || [ ! -f "$candidate" ]; then
  echo "failed to locate libclang_rt.fuzzer.a under ${clang_lib_root}" >&2
  exit 1
fi

mkdir -p "$(dirname "$dst_path")"
cp "$candidate" "$dst_path"
