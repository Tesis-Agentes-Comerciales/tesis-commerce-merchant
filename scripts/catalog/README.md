# Recolección del catálogo (uso único)

Estos scripts armaron el surtido del merchant en septiembre de 2026. Consultan servicios externos
y reescriben dos artefactos versionados: el snapshot `src/commerce_lab/fixtures/catalog_snapshot.json`
y las fotos de `assets/products/`, con su atribución en `assets/products/PROVENANCE.md`. El merchant
nunca usa la red: lee solo esos archivos, y `import-current` los carga una vez en PostgreSQL.
No hace falta ejecutarlos para desarrollar, probar ni desplegar; volver a correrlos cambia el
catálogo y exige revisar el diff, la procedencia y las licencias antes de commitear.

| Script | Qué hace |
|---|---|
| `fetch_wikidata_catalog.py --out src/commerce_lab/fixtures/catalog_snapshot.json` | Recolecta modelos reales desde Wikidata, con el nombre del archivo de Wikimedia Commons de cada imagen. |
| `fetch_product_images.py` | Descarga una vez las fotos de Commons a `assets/products/` y escribe `PROVENANCE.md`, para que la tienda las sirva desde su propio origen. |
| `fetch_render_images.py` | Reemplaza fotos de usuario por el render oficial del fabricante cuando su CDN lo tiene; registra esos renders aparte en `PROVENANCE.md`. |
| `fetch_store_lineup.py` | Completa el surtido de una cadena con las capacidades reales de cada modelo y el render del fabricante, con el descargador de `fetch_render_images.py`. Su búsqueda en Openverse (`_best_image`) quedó definida pero ya no se llama. |

Se ejecutan desde la raíz con `uv run python scripts/catalog/<script>`. `fetch_store_lineup.py`
importa `fetch_render_images.py`, así que ambos deben seguir en el mismo directorio.
