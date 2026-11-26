#!/usr/bin/env bash
# Compuerta local: lo que debe pasar antes de integrar un cambio.
#
#   scripts/check.sh        lock, formato, lint, tipos y pruebas sin PostgreSQL
#   scripts/check.sh --db   lo anterior y la capa de integración contra un PostgreSQL desechable
set -euo pipefail

cd "$(dirname "$0")/.."

with_db=false
case "${1:-}" in
  "") ;;
  --db) with_db=true ;;
  *)
    echo "Uso: scripts/check.sh [--db]" >&2
    exit 2
    ;;
esac

step() {
  echo "==> $*"
  "$@"
}

step uv lock --check
step uv run ruff format --check .
step uv run ruff check .
step uv run pyright
step env -u RUN_DB_INTEGRATION uv run pytest -q

if "$with_db"; then
  # El servicio `postgres-test` de compose.yaml: otro puerto, datos en memoria y sin volumen.
  export MERCHANT_TEST_PG_PORT="${MERCHANT_TEST_PG_PORT:-55434}"
  test_db() { docker compose --profile test "$@"; }
  # Al salir, con o sin error, se apaga y se quita el contenedor; la base de desarrollo no se toca.
  trap 'test_db stop postgres-test && test_db rm --force postgres-test' EXIT
  # --force-recreate: cada corrida empieza con una base vacía.
  step test_db up --detach --wait --force-recreate postgres-test

  export RUN_DB_INTEGRATION=1
  export DATABASE_URL="postgresql://tesis_dev:tesis_local_only@127.0.0.1:${MERCHANT_TEST_PG_PORT}/tesis_lab"
  # Las pruebas de recuperación solo corren sobre el clúster cuyo directorio de datos se nombra aquí.
  export RECOVERY_TEST_CLUSTER=/var/lib/postgresql/18/docker
  # La prueba de migraciones necesita la base recién creada, así que va primero.
  step uv run pytest -q tests/integration/test_migration_tracking.py
  step uv run pytest -q tests/integration --ignore=tests/integration/test_migration_tracking.py
fi

echo "Compuerta en verde en ${SECONDS} s."
