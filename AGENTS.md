# Comercio sintético

Este repositorio ejecuta el comercio ACP REST local de la tesis. Python 3.12 y uv: use `uv sync`,
no `pip`, y no edite `uv.lock` a mano.

PostgreSQL es la fuente autoritativa de catálogo, checkout, idempotencia, órdenes y eventos. Use
SQL explícito con Psycopg; un ORM requiere una decisión técnica justificada. Mantenga el dominio
interno separado de `ACPCheckoutAdapter`.

ACP está fijado en 2026-04-17: no modifique `vendor/acp/2026-04-17`, cuyos bytes se prueban por
hash. Solo hay datos y credenciales sintéticos y locales: sin dinero real, credenciales de pago
reales ni LLM dentro del comercio. El cliente nunca fija el precio autoritativo y todo POST con
efecto debe ser idempotente. El único medio de pago es el handler sintético `tesis_sandbox`, que
confirma el proveedor sandbox independiente. El agente comprador, el LLM y la interfaz viven en
`tesis-commerce-agent`; no los incorpore aquí.

La base se publica solo en 127.0.0.1:55433 y FastAPI solo en 127.0.0.1:4120. El agente levanta
este repositorio con `uv run demo-stack`: antes de cambiar el paquete `commerce_lab`,
`compose.yaml`, los comandos de `commerce_lab.db` y `commerce_lab.managed_catalog`, el puerto, una
ruta o una variable, busque quién la usa en ese repositorio. No renumere migraciones ni quite fotos
de `assets/products/`, porque cambian el hash del catálogo.

`src/commerce_lab` es el producto y `tests/` su verificación; `tests/integration/` necesita un
PostgreSQL desechable, nunca el volumen de desarrollo. `scripts/catalog/` son recolectores de uso
único: no se ejecutan para desarrollar ni probar. No hay integración continua: antes de entregar
corra `scripts/check.sh`, y `scripts/check.sh --db` cuando el cambio pase por PostgreSQL (ver
`docs/testing.md`).
