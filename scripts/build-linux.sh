#!/usr/bin/env bash
set -euo pipefail

opennavicat_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
opennavicat_output="${1:-$opennavicat_root/artifacts/linux}"
mkdir -p "$opennavicat_output"
opennavicat_output="$(cd -- "$opennavicat_output" && pwd)"
opennavicat_image="opennavicat-linux-builder:local"
docker build --file "$opennavicat_root/scripts/Dockerfile.linux" \
  --tag "$opennavicat_image" "$opennavicat_root/scripts"
opennavicat_options=(--rm)
if [[ -n "${OPENNAVICAT_TEST_POSTGRES_PORT:-}" || -n "${OPENNAVICAT_TEST_MYSQL_PORT:-}" || -n "${OPENNAVICAT_TEST_MARIADB_PORT:-}" ]]; then
  # Optional disposable fixtures on the Docker host; never discover or use
  # existing user database profiles or forward arbitrary host environment.
  opennavicat_options+=(--network host)
  for opennavicat_variable in OPENNAVICAT_TEST_POSTGRES_PORT OPENNAVICAT_TEST_MYSQL_PORT OPENNAVICAT_TEST_MARIADB_PORT; do
    if [[ -n "${!opennavicat_variable:-}" ]]; then
      opennavicat_options+=(--env "$opennavicat_variable=${!opennavicat_variable}")
    fi
  done
fi
docker run \
  "${opennavicat_options[@]}" \
  --mount "type=bind,source=$opennavicat_root,target=/src,readonly" \
  --mount "type=bind,source=$opennavicat_output,target=/out" \
  "$opennavicat_image" 2>&1 | tee "$opennavicat_output/build-linux.log"
printf 'Linux test/build artifacts: %s\n' "$opennavicat_output"
