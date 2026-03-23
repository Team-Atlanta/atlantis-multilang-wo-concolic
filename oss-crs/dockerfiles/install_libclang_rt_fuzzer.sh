#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <archive-path>" >&2
  exit 1
fi

archive_path="$1"
clang_bin="${CLANG_BIN:-clang}"
target_triple_dir="${TARGET_TRIPLE_DIR:-$(uname -m)-unknown-linux-gnu}"
resource_dir="$("$clang_bin" --print-resource-dir)"
dst_path="${resource_dir}/lib/${target_triple_dir}/libclang_rt.fuzzer.a"

mkdir -p "$(dirname "$dst_path")"
install -m 0644 "$archive_path" "$dst_path"
