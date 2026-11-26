# Pruebas

Cómo se verifica el comercio antes de integrar un cambio. Todo corre en local con
`scripts/check.sh`. Las pruebas de sistema que cruzan el agente, este comercio y el proveedor de
pagos viven en el repositorio del agente.

## La compuerta

```sh
scripts/check.sh        # capa 1
scripts/check.sh --db   # capas 1 y 2
```

Los pasos corren en este orden y la compuerta se detiene en el primer fallo:

1. `uv lock --check`: el lock coincide con `pyproject.toml`.
2. `uv run ruff format --check .` y `uv run ruff check .`: formato y lint.
3. `uv run pyright`: tipos de `src/` y `tests/`.
4. `uv run pytest -q`: pruebas unitarias y de contrato. `tests/integration/` se recoge y se omite.

Con `--db`, además:

5. Levanta el servicio `postgres-test` de `compose.yaml`: la misma imagen fijada que la base de
   desarrollo, en el puerto 55434 (`MERCHANT_TEST_PG_PORT` lo cambia), con los datos en memoria y
   sin volumen. Lo recrea en cada corrida, así que siempre empieza con una base vacía.
6. Corre primero la prueba de migraciones, que necesita la base recién creada, y luego el resto de
   `tests/integration/`.
7. Al salir, con o sin error, apaga y quita el contenedor. La base de desarrollo (servicio
   `postgres`, volumen `merchant_pgdata`) no se toca.

## Capas

| Capa | Qué verifica | Dónde | Pruebas | Tiempo |
| --- | --- | --- | --- | --- |
| 1. Unitarias y contratos | Catálogo, feed, ACP y AP2 contra los esquemas congelados, webhooks, cliente de pagos y la API sin base | `tests/` sin `integration/` | 228 | ~2 s |
| 2. PostgreSQL | Migraciones, checkout, completar, creación guardada y su consulta, recuperación de la creación, catálogo persistente y administrado, entrega de webhooks y ACP por HTTP | `tests/integration/` | 233 | ~90 s |

En un Mac con Apple Silicon, `scripts/check.sh` tarda unos 5 s y `scripts/check.sh --db`, menos de
dos minutos.

## Cómo se separan

`tests/conftest.py` marca todo `tests/integration/` con `db` y lo omite salvo con
`RUN_DB_INTEGRATION=1`, así que un módulo nuevo en esa carpeta queda protegido sin `skipif`
propio. Las pruebas de creación guardada y de recuperación exigen además `RECOVERY_TEST_CLUSTER`
con el directorio de datos del clúster, y se niegan a correr sobre otro. `check.sh --db` fija esas
dos variables y `DATABASE_URL`.

El marcador `mutates_checkout_state` señala las pruebas que actualizan, cancelan, completan o
reescriben un checkout, su orden o su evidencia de creación. Un filtro por nombre con `-k` no las
excluye; para una selección parcial que no mute checkouts, combínelo con el marcador. Por ejemplo,
contra la base de prueba:

```sh
docker compose --profile test up -d --wait --force-recreate postgres-test
RUN_DB_INTEGRATION=1 RECOVERY_TEST_CLUSTER=/var/lib/postgresql/18/docker \
  DATABASE_URL=postgresql://tesis_dev:tesis_local_only@127.0.0.1:55434/tesis_lab \
  uv run pytest tests/integration -k lookup -m "not mutates_checkout_state"
docker compose --profile test rm --stop --force postgres-test
```

Aun así, toda la capa 2 crea corridas y checkouts y cambia el catálogo administrado: solo debe
correr contra una base desechable como esta, nunca contra la de desarrollo.

## Al escribir una prueba

- Lo que abra PostgreSQL va en `tests/integration/`; el resto no debe conectarse.
- Escribir solo en `tmp_path` o en la base desechable, nunca en el checkout del repositorio.
- Fuera de la prueba de migraciones, no depender del orden ni del estado que deje otra prueba de
  la capa 2: cada una crea sus corridas y checkouts.
- No medir tiempos con umbrales absolutos ni depender del día real sin tolerar el cambio de fecha.
- Fijar hashes solo de contratos congelados: los esquemas ACP de `vendor/acp`, que verifica
  `tests/contracts/test_acp.py`, y el catálogo base que siembran las evaluaciones del agente, que
  verifica `tests/test_catalogo_ampliado.py`.
