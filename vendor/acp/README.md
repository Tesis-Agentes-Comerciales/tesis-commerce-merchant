# ACP 2026-04-17 congelado

Copia sin modificar de seis archivos del repositorio oficial del
[Agentic Commerce Protocol](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol),
en el commit `7fdd78df677a94dce04c770644b0fbbb1401272b`.

| Archivo en `2026-04-17/` | Para qué |
|---|---|
| `schema.agentic_checkout.json` | Esquema de checkout y discovery: el comercio valida contra él create, get, update y cancel y `/.well-known/acp.json` |
| `schema.feed.json` | Esquema del Product Feed: el comercio valida contra él los metadatos y cada producto del feed |
| `openapi.agentic_checkout.yaml` y `openapi.feed.yaml` | Contratos HTTP de referencia del checkout y del feed |
| `openapi.agentic_checkout_webhook.yaml` | Contrato de los webhooks de órdenes |
| `LICENSE` | Licencia Apache-2.0 del protocolo |

`provenance.json` registra la ruta de origen y el SHA-256 de cada archivo, y
`tests/contracts/test_acp.py` comprueba esos bytes. `.gitattributes` desactiva la conversión de
saltos de línea para que los hashes coincidan en cualquier sistema. Estos archivos no se editan.

El comercio implementa un subconjunto sintético del protocolo, descrito en
[docs/protocolo.md](../../docs/protocolo.md), sin afirmar conformidad completa.
