# Tesis Commerce Merchant

Comercio sintético de una tesis de pregrado sobre agentes de compra: una API que vende smartphones
a un agente por el protocolo ACP, con catálogo, checkout y órdenes en PostgreSQL y sin dinero real.

## Cómo correrlo

Lo normal es no arrancarlo a mano. Clone los tres repositorios lado a lado, con estos nombres
exactos, y desde `tesis-commerce-agent` corra `uv run demo-stack`: levanta la base de este
comercio, sus migraciones y su catálogo, el proveedor de pagos, el agente y la interfaz. Requisitos
y opciones en el [README del agente](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent#readme).

```sh
git clone https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent.git
git clone https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-merchant.git
git clone https://github.com/Tesis-Agentes-Comerciales/tesis-payment-provider-sandbox.git
```

### Arranque manual

Requiere Python 3.12, [uv](https://docs.astral.sh/uv/) y Docker. Desde la raíz de este repositorio:

```sh
uv sync --frozen
docker compose up -d --wait postgres                           # PostgreSQL en 127.0.0.1:55433
uv run python -m commerce_lab.db migrate
uv run python -m commerce_lab.managed_catalog import-current    # imprime el run_id del catálogo
uv run python -m commerce_lab.db issue-session <run_id> merchant-agent
uv run uvicorn commerce_lab.api:app --host 127.0.0.1 --port 4120
```

`issue-session` toma el `run_id` que imprimió `import-current` y devuelve un Bearer sintético de
una hora para `merchant-agent`, el actor dueño del catálogo; no hay endpoint para emitirlo. Con eso
ya se consulta el catálogo y se preparan checkouts. Completar compras y AP2 necesitan las variables
de [`.env.example`](.env.example); sin ellas, esas rutas fallan cerrado. `DATABASE_URL` solo acepta
`tesis_lab` en localhost (`MERCHANT_ALLOW_REMOTE_DB=true` lo amplía solo a `*.railway.internal`).
ACP, AP2 y el manejo de la moneda se detallan en [docs/protocolo.md](docs/protocolo.md).

| Comando (`uv run python -m commerce_lab.…`) | Para qué |
| --- | --- |
| `managed_catalog update-offer <id> --price-minor N --stock N` | Cambia el precio (en centavos de COP) o el stock de una variante |
| `db webhook-dispatch --limit N` | Entrega hasta N webhooks de órdenes pendientes (uno por defecto); el comercio no tiene un proceso propio que lo haga |
| `db order-update <run_id> <actor_id> <order_id>` | Pasa una orden de confirmada a en proceso |
| `db run-checkouts <run_id>` | Lista los checkouts y las órdenes de una corrida |

## Catálogos

Hay dos catálogos de smartphones, armados una sola vez con modelos de Wikidata y fotos libres de
Wikimedia Commons (procedencia y licencias en [`scripts/catalog/`](scripts/catalog/README.md)).
Precio, stock, colores, envío y plazo de entrega salen de un generador determinista.

| Catálogo | Tamaño | Uso |
| --- | --- | --- |
| Base (`--catalog p0`) | 141 productos, 310 variantes | El predeterminado: demo, pruebas y evaluaciones del agente |
| Ampliado (`--catalog ampliado`) | 287 productos, 606 variantes | Opcional: suma otras marcas y más gama media y baja |

`import-current` carga el base una sola vez y repetirlo no toca precios ni stock; con `--catalog
ampliado` agrega los modelos que falten. Las evaluaciones del agente usan siempre el base, para que
respuestas como «el teléfono negro más barato» sigan siendo comparables, y
`tests/test_catalogo_ampliado.py` fija su hash.

## Pruebas

```sh
scripts/check.sh        # lock, formato, lint, tipos y pruebas sin base de datos
scripts/check.sh --db   # lo anterior y la capa de integración contra PostgreSQL
```

`--db` usa una base desechable (`postgres-test` en `compose.yaml`, en memoria y en el puerto 55434)
y la apaga al terminar. Capas, cifras y reglas en [docs/testing.md](docs/testing.md).

## Estructura

| Ruta | Contenido |
| --- | --- |
| `src/commerce_lab/` | El comercio, en el paquete Python `commerce_lab`: API, adaptador ACP, checkout, catálogo, AP2 y webhooks |
| `migrations/` | SQL de PostgreSQL que aplica `db migrate`, en orden |
| `vendor/acp/` | Esquemas ACP `2026-04-17` congelados, con sus hashes ([README](vendor/acp/README.md)) |
| `assets/products/` | Fotos del catálogo, servidas en `/assets`, con su procedencia en `PROVENANCE.md` |
| `tests/` | Pruebas unitarias y de contrato; `tests/integration/` necesita PostgreSQL |
| `scripts/` | La compuerta `check.sh` y los recolectores del catálogo ([README](scripts/README.md)) |
| `docs/` | [Protocolo](docs/protocolo.md) y [pruebas](docs/testing.md) |
