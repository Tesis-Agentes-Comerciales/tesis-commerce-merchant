"""Descarga una vez las fotos del catálogo y las deja servidas por el propio comercio.

Enlazar a Wikimedia desde la vitrina no funciona: `Special:FilePath` redirige a través del
wiki, que limita las peticiones, y una retícula pide decenas de imágenes a la vez. Al
descargarlas el comercio las sirve desde su propio origen y la tienda deja de depender de
un tercero.

Las imágenes son de Wikimedia Commons, con licencias libres. Se escribe PROVENANCE.md con el
archivo de origen de cada una para poder citarlas.

Con `--ampliado` descarga las fotos del catálogo ampliado con el mismo procedimiento y solo
reescribe su sección de PROVENANCE.md, con licencia y autor de cada archivo; el resto del
archivo queda igual.

Uso:
    uv run python scripts/catalog/fetch_product_images.py
    uv run python scripts/catalog/fetch_product_images.py --ampliado
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[2]
SNAPSHOT: Final = ROOT / "src/commerce_lab/fixtures/catalog_snapshot.json"
SNAPSHOT_AMPLIADO: Final = ROOT / "src/commerce_lab/fixtures/catalog_snapshot_ampliado.json"
OUT_DIR: Final = ROOT / "assets/products"
USER_AGENT: Final = "TesisCommerceCatalog/1.0 (Uniandes thesis; academic use)"
WIDTH: Final = 480
# Pausa entre peticiones para no presionar a Wikimedia.
DELAY_SECONDS: Final = 0.25
AMPLIADO_HEADING: Final = "## Catálogo ampliado"

_EXTENSIONS: Final = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".png",
}


def _source_url(file_name: str) -> str:
    quoted = urllib.parse.quote(file_name.replace(" ", "_"))
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quoted}?width={WIDTH}"


def _download(url: str) -> tuple[bytes, str] | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read(), response.headers.get_content_type()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _fetch_all(products: list[dict[str, Any]]) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    """Descarga lo que falte y devuelve (archivo servido, producto) de cada foto disponible."""
    served: list[tuple[str, dict[str, Any]]] = []
    saved = skipped = failed = 0
    for index, product in enumerate(products, start=1):
        entity = str(product["entity"])
        source_file = str(product["image_file"])
        existing = list(OUT_DIR.glob(f"{entity}.*"))
        if existing:
            skipped += 1
            served.append((existing[0].name, product))
            continue

        result = None
        for attempt in range(3):
            result = _download(_source_url(source_file))
            if result is not None:
                break
            time.sleep(1.5 * (attempt + 1))
        if result is None:
            failed += 1
            print(f"  [{index}/{len(products)}] FALLO {entity} {source_file}", flush=True)
            continue

        body, content_type = result
        target = OUT_DIR / f"{entity}{_EXTENSIONS.get(content_type, '.jpg')}"
        target.write_bytes(body)
        saved += 1
        served.append((target.name, product))
        if index % 25 == 0:
            print(f"  [{index}/{len(products)}] descargadas={saved} fallos={failed}", flush=True)
        time.sleep(DELAY_SECONDS)
    print(json.dumps({"descargadas": saved, "ya_estaban": skipped, "fallos": failed}))
    return served, failed


def _write_p0_provenance(served: list[tuple[str, dict[str, Any]]]) -> None:
    header = [
        "# Procedencia de las imágenes de producto",
        "",
        "Todas provienen de **Wikimedia Commons** y se descargaron una sola vez con",
        "`scripts/catalog/fetch_product_images.py`. Cada archivo conserva la licencia libre de su",
        "original; la página del archivo en Commons documenta autoría y licencia exacta:",
        "",
        "    https://commons.wikimedia.org/wiki/File:<archivo de origen>",
        "",
        "| Archivo servido | Producto | Archivo en Commons |",
        "| --- | --- | --- |",
    ]
    rows = [
        f"| `{name}` | {product['title']} | `{product['image_file']}` |" for name, product in served
    ]
    (OUT_DIR / "PROVENANCE.md").write_text("\n".join([*header, *rows, ""]), encoding="utf-8")


def _write_ampliado_provenance(served: list[tuple[str, dict[str, Any]]]) -> None:
    """Reemplaza solo la sección del catálogo ampliado; las demás quedan como estaban."""
    section = [
        AMPLIADO_HEADING,
        "",
        "Fotos de los modelos de `src/commerce_lab/fixtures/catalog_snapshot_ampliado.json`,",
        "descargadas una sola vez de **Wikimedia Commons** con",
        "`scripts/catalog/fetch_product_images.py --ampliado`. Son archivos con licencia libre; la",
        "página del archivo en Commons documenta autoría y licencia exacta.",
        "",
        "| Archivo servido | Producto | Archivo en Commons | Licencia | Autor |",
        "| --- | --- | --- | --- | --- |",
        *(
            f"| `{name}` | {product['title']} | `{product['image_file']}` "
            f"| {product.get('image_license', '')} | {product.get('image_author', '')} |"
            for name, product in sorted(served, key=lambda pair: pair[0])
        ),
    ]
    path = OUT_DIR / "PROVENANCE.md"
    before, _, rest = path.read_text(encoding="utf-8").partition(f"\n{AMPLIADO_HEADING}\n")
    following = rest.find("\n## ")
    after = rest[following:] if following >= 0 else ""
    body = before.rstrip("\n") + "\n\n" + "\n".join(section) + "\n" + after
    path.write_text(body, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(prog="fetch_product_images")
    parser.add_argument("--ampliado", action="store_true")
    arguments = parser.parse_args()

    snapshot = SNAPSHOT_AMPLIADO if arguments.ampliado else SNAPSHOT
    products: list[dict[str, Any]] = json.loads(snapshot.read_text(encoding="utf-8"))["products"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    served, failed = _fetch_all(products)
    if arguments.ampliado:
        _write_ampliado_provenance(served)
    else:
        _write_p0_provenance(served)
    if failed and arguments.ampliado:
        raise SystemExit(f"{failed} fotos no se descargaron; vuelva a correr el script")


if __name__ == "__main__":
    main()
