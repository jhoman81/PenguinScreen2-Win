#!/usr/bin/env bash
# Build `validate`: PenguinScreen2's real profile loader (pcsx2/VR/VRProfileDB.cpp) as a
# standalone command-line checker, so a profile can be checked without starting the emulator.
#   tools/gt4vr/dev/build-validator.sh
#   tools/gt4vr/dev/build/validate bin/resources <folder with your yaml> SCUS-97328 77E61C8A
# Linux with g++ 11+, cmake and git. Fetches rapidyaml v0.12.1 (the version the flatpak pins)
# unless RYML_PREFIX points at an existing install.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
R=$(cd "$HERE/../../.." && pwd)
OUT=${OUT:-$HERE/build}
mkdir -p "$OUT/obj"
RYML=${RYML_PREFIX:-$OUT/ryml}
if [ ! -f "$RYML/lib/libryml.a" ]; then
  rm -rf "$OUT/rapidyaml-src" "$OUT/rapidyaml-build"
  git clone --quiet --depth 1 --branch v0.12.1 --recursive https://github.com/biojppm/rapidyaml.git "$OUT/rapidyaml-src"
  cmake -S "$OUT/rapidyaml-src" -B "$OUT/rapidyaml-build" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$RYML" -DCMAKE_POSITION_INDEPENDENT_CODE=ON -Wno-dev >/dev/null
  cmake --build "$OUT/rapidyaml-build" -j"$(nproc)" --target install >/dev/null
fi
FLAGS="-std=c++20 -O1 -w -msse4.1 -DPCSX2_CORE -I$R -I$R/pcsx2 -I$R/common -I$R/3rdparty/fmt/include
       -I$R/3rdparty/fast_float/include -I$RYML/include -I$R/3rdparty/include"
SRCS="common/Assertions.cpp common/Console.cpp common/Error.cpp common/FileSystem.cpp common/SmallString.cpp
      common/StringUtil.cpp common/Timer.cpp common/YAML.cpp pcsx2/VR/VRProfileDB.cpp pcsx2/VR/SpatialControls.cpp
      3rdparty/fmt/src/format.cc 3rdparty/fmt/src/os.cc"
for f in $SRCS; do
  g++ $FLAGS -c "$R/$f" -o "$OUT/obj/$(basename "$f").o"
done
g++ $FLAGS -c "$HERE/validate.cpp" -o "$OUT/obj/validate.o"
g++ $FLAGS -c "$HERE/stubs.cpp" -o "$OUT/obj/stubs.o"
g++ "$OUT"/obj/*.o -L"$RYML/lib" -lryml -lc4core -o "$OUT/validate"
echo "built $OUT/validate"
