# Merchant experimental

Este repositorio ejecuta únicamente el merchant ACP REST local. Python 3.12 y uv; use
`uv sync`, no `pip`, y no edite `uv.lock` a mano.

PostgreSQL es la fuente autoritativa de catálogo, checkout, idempotencia y eventos.
Use SQL explícito con Psycopg; un ORM requiere decisión técnica justificada.
Mantenga separado el dominio interno de `ACPCheckoutAdapter`.

ACP está fijado en 2026-04-17. No modifique archivos en
`vendor/acp/2026-04-17`. El servicio usa únicamente datos y credenciales
sintéticos locales: sin dinero real, credenciales reales ni LLM dentro del merchant.
El cliente no fija precio autoritativo. Todo POST con efecto debe ser idempotente.

La base se publica solo en 127.0.0.1:55433 y FastAPI solo en 127.0.0.1:4120.
No use credenciales de pago reales ni dinero real. La capability de pago
sintética `tesis_sandbox` está permitida. No incorpore procesamiento de pagos,
`/complete`, Order, decremento de inventario ni webhooks, salvo autorización
explícita del ticket actual. No incorpore Buyer Agent, LangGraph, navegador ni
OPA en esta extracción.

`src/commerce_lab` es el producto y `tests/` su verificación; `tests/integration/` necesita un
PostgreSQL desechable y `RUN_DB_INTEGRATION=1`, nunca el volumen de desarrollo. `scripts/catalog/`
son recolectores de uso único: no se ejecutan para desarrollar ni probar. Antes de entregar,
corra `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright` y `uv run pytest`,
y la capa 2 como indica `docs/testing.md` cuando el cambio pase por PostgreSQL.
