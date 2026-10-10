#!/usr/bin/env bash
# Install the external solvers the Krenn–Gu repository's certificate chains use:
# Singular, msolve, nauty (apt); kissat, glucose, drat-trim (built from GitHub); CaDiCaL (apt).
# Safe to re-run. Prints what ended up on PATH.
set -u
SUDO=""; [ "$(id -u)" != 0 ] && command -v sudo >/dev/null && SUDO="sudo"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq >/dev/null 2>&1 || true
$SUDO apt-get install -y -qq singular msolve nauty cadical build-essential cmake zlib1g-dev >/dev/null 2>&1 \
  || echo "apt install failed (no network to the Ubuntu mirrors?)"

SRC="${SOLVER_SRC:-$HOME/.solvers-src}"; BIN="${SOLVER_BIN:-/usr/local/bin}"; mkdir -p "$SRC"
build() {  # name, repo, build commands, produced binary path
  local name=$1 repo=$2 cmds=$3 out=$4
  command -v "$name" >/dev/null && return
  ( cd "$SRC" && rm -rf "$name" && git clone -q --depth 1 "https://github.com/$repo" "$name" && cd "$name" && eval "$cmds" ) >/dev/null 2>&1 \
    && $SUDO install -m 755 "$SRC/$name/$out" "$BIN/$name" || echo "build failed: $name"
}
build kissat    arminbiere/kissat   "./configure && make -j4"            build/kissat
build drat-trim marijnheule/drat-trim "make"                             drat-trim
build glucose   audemard/glucose    "cd simp && make rs"                 simp/glucose_static

echo "== solvers on PATH"
for b in Singular msolve kissat glucose drat-trim cadical dreadnaut geng; do
  printf '%-10s %s\n' "$b" "$(command -v $b || echo MISSING)"
done
