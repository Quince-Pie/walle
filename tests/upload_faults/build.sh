#!/usr/bin/env bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project=$(cd -- "$source_dir/../.." && pwd)
out="$project/build/tests/upload-faults"
mkdir -p "$out"
read -r -a flags <<< "$(pkg-config --cflags vulkan)"
${CC:-cc} -std=c23 -O2 -fPIC -shared -Wall -Wextra -Wpedantic \
    "$source_dir/submit_fault.c" "${flags[@]}" -ldl -o "$out/submit_fault.so"
