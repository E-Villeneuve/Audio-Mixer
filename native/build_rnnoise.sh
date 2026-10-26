#!/bin/sh
# Rebuilds native/rnnoise.dll from the official xiph/rnnoise source.
#
# Needs git, curl and a Zig compiler (e.g. `pip install --target <dir> ziglang`).
# Usage:  ZIG=/path/to/zig.exe sh native/build_rnnoise.sh
#
# Pinned inputs (what the shipped DLL was built from):
#   source  https://github.com/xiph/rnnoise @ 70f1d256acd4b34a572f999a05c87bf00b67730d
#   model   rnnoise_data-<model_version>.tar.gz from media.xiph.org, sha256 checked
#           against the model_version file in that commit
#   zig     0.15.2
set -e

COMMIT=70f1d256acd4b34a572f999a05c87bf00b67730d
ZIG=${ZIG:-zig}
OUT=$(cd "$(dirname "$0")" && pwd)/rnnoise.dll
WORK=$(mktemp -d)

git clone -q https://github.com/xiph/rnnoise.git "$WORK/rnnoise"
cd "$WORK/rnnoise"
git checkout -q "$COMMIT"

hash=$(cat model_version)
model=rnnoise_data-$hash.tar.gz
curl -sSfL -o "$model" "https://media.xiph.org/rnnoise/models/$model"
[ "$(sha256sum "$model" | cut -d' ' -f1)" = "$hash" ] || { echo "model checksum mismatch"; exit 1; }
tar xzomf "$model"

# Same sources as librnnoise in Makefile.am, portable C (no SSE4.1/AVX2 dispatch).
"$ZIG" cc -target x86_64-windows-gnu -shared -O2 -DNDEBUG -DWIN32 \
    -DRNNOISE_BUILD -DDLL_EXPORT -Iinclude -Isrc \
    src/denoise.c src/rnn.c src/pitch.c src/kiss_fft.c src/celt_lpc.c \
    src/nnet.c src/nnet_default.c src/parse_lpcnet_weights.c \
    src/rnnoise_data.c src/rnnoise_tables.c \
    -o "$OUT"

rm -rf "$WORK"
sha256sum "$OUT"
