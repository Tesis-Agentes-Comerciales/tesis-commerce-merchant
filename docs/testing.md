# Estrategia de pruebas

Qué se prueba en el Merchant, dónde vive cada prueba, cuándo corre y cómo ejecutarla en local.
Las pruebas de sistema que cruzan el Agent, este Merchant y el proveedor de pagos viven en el
repositorio del Agent (`tests/integration`, capa 4 de su `docs/testing.md`).

## Capas

| Capa | Qué verifica | Dónde | Cuándo |
| --- | --- | --- | --- |
| 1. Unitarias y contratos | Catálogo, feed, ACP y AP2 (incluido el esquema ACP vendorizado), webhooks, cliente de pagos, API sin base | `tests/` sin `integration/` | Cada PR (`unit`) |
| 2. PostgreSQL | Migraciones, checkout, completion, D17 (creación y lookup guardados), catálogo persistente y gestionado, entrega de webhooks, ACP por HTTP | `tests/integration/` | Cada PR (`integration`) |
| Storefront | Lint y build con tipos | `storefront/` | Cada PR (`storefront`) |

`tests/conftest.py` marca todo `tests/integration/` con `db` y lo omite salvo con
`RUN_DB_INTEGRATION=1`; un módulo nuevo en esa carpeta queda protegido sin `skipif` propio.
`mutates_checkout_state` sigue marcando las pruebas que actualizan, cancelan, completan o
reescriben un checkout persistido (ver el README).

## En local

Capa 1, sin PostgreSQL:

```sh
uv run pytest
```

Capa 2, contra un PostgreSQL desechable y recién creado, nunca el volumen de desarrollo. Con la
misma imagen y `PGDATA` del CI, en un puerto libre:

```sh
docker run -d --name merchant-test-pg -p 127.0.0.1:55499:5432 \
  -e POSTGRES_USER=tesis_dev -e POSTGRES_PASSWORD=tesis_local_only -e POSTGRES_DB=tesis_lab \
  -e PGDATA=/var/lib/postgresql/18/docker \
  postgres:18.4-alpine@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15
export DATABASE_URL=postgresql://tesis_dev:tesis_local_only@127.0.0.1:55499/tesis_lab \
  RUN_DB_INTEGRATION=1 RECOVERY_TEST_CLUSTER=/var/lib/postgresql/18/docker
uv run pytest tests/integration/test_migration_tracking.py   # primero: necesita la base vacía
uv run pytest --ignore=tests/integration/test_migration_tracking.py
docker rm --force --volumes merchant-test-pg
```

Las suites D17 rechazan cualquier clúster cuyo `data_directory` no sea `RECOVERY_TEST_CLUSTER`.

La prueba de indisponibilidad INF31 se ejecuta explícitamente al final del job
`integration`. Usa HTTP real y detiene únicamente el contenedor PostgreSQL
desechable de ese job, después de comprobar el puerto y el directorio de datos.
Comprueba el 503 nativo con `Request-Id` del servidor, reinicia PostgreSQL y
contrasta los estados persistidos. El intento de auditoría durante la caída
queda en el log del servicio, sin fila persistida. El job conserva JUnit y su
SHA-256 y exige que el caso se ejecute y apruebe.

La clasificación de INF31 cubre la negativa TCP observada al abrir la conexión
para verificar la sesión. El diagnóstico de libpq carece de SQLSTATE en ese
caso. Se reconoce la forma observada en el laboratorio en inglés. Los fallos
de autenticación de la base, los diagnósticos ambiguos y los errores de consulta
o transacción conservan el tratamiento anterior.
La forma local de macOS que informa `could not receive data from server: Connection refused`
también se reconoce solo durante la apertura de autenticación, sin reclasificar errores mixtos.

Para repetir la capa 2, recree el contenedor: la prueba de migraciones solo pasa sobre una base
vacía.

## Workflow

`ci.yml` corre en cada PR y en `main`, con `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` (`pytest-cov` se
carga con `-p pytest_cov`):

- `lint`: `ruff format --check`, `ruff check`, `pyright` y `uv build`.
- `unit`: `uv run pytest` con cobertura; `tests/integration` se recoge y se omite.
- `integration`: servicio PostgreSQL con la imagen fijada; la prueba de migraciones primero y
  luego toda la suite (unitarias incluidas) en serie, con cobertura acumulada y umbral.
- `storefront`: `npm ci`, `npm run lint` y `npm run build`. El storefront aún no tiene pruebas.

Cada job de pruebas publica su tabla de cobertura en el resumen de la ejecución.

## Cobertura

Se mide con `pytest-cov` sobre `commerce_lab`, con ramas (`[tool.coverage.*]` en
`pyproject.toml`). Línea base del 2026-10-20:

| Selección | Pruebas | Tiempo | Cobertura | Líneas | Ramas |
| --- | --- | --- | --- | --- | --- |
| Capa 1 | 175 (226 omitidas) | ~3 s | 53.3 % | 58.9 % | 31.0 % |
| Capas 1 y 2 | 401 | ~70 s | 87.0 % | 89.8 % | 75.7 % |

El umbral del CI es 85 % sobre el total del job `integration`: la capa 1 sola no puede llegar
porque casi todo el Merchant pasa por PostgreSQL, así que no lleva umbral propio.

## Al escribir una prueba

- Lo que abra PostgreSQL va en `tests/integration/`; el resto no debe conectarse.
- Escribir solo en `tmp_path` o en la base desechable, nunca en el checkout.
- Fuera de la migración inicial, no depender del orden ni del estado que deje otra prueba de la
  capa 2: cada una crea sus episodios y checkouts.
- No medir tiempos con umbrales absolutos ni depender del día real sin tolerar el cambio de
  fecha.
- Fijar hashes solo de contratos congelados (el esquema ACP vendorizado en `vendor/acp`, que
  verifica `tests/contracts/test_acp.py`).
