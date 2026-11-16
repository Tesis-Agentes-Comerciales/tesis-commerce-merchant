"""Catálogo ampliado: el surtido P0 más modelos de marcas y gamas que P0 no tiene.

Es opcional y solo lo carga el catálogo administrado cuando se pide explícitamente
(`managed_catalog import-current --catalog ampliado`). Las semillas de prueba y evaluación
(`db.seed`, `verify()`, el hash del fixture y `FIXTURE_VERSION`) siguen usando únicamente P0:
agregar productos allí cambiaría las respuestas correctas de los casos y volvería
incomparables los resultados oficiales.

Los modelos nuevos salen de su propio snapshot de Wikidata, con fotos de Wikimedia Commons, y
pasan por el mismo generador determinista que P0: precio, inventario, colores, condición,
capacidades, envío y plazo se derivan igual del identificador de cada modelo.
"""

from pathlib import Path
from typing import Final

from commerce_lab.contracts import Offer
from commerce_lab.fixtures.generated import build_generated_offers
from commerce_lab.fixtures.p0 import (
    FIXTURE_DELIVERY_CONTEXT,
    FIXTURE_EXPIRES_AT,
    FIXTURE_NOW,
    fresh_p0_offers,
)

AMPLIADO_SNAPSHOT_PATH: Final = Path(__file__).with_name("catalog_snapshot_ampliado.json")


def ampliado_offers() -> list[Offer]:
    """P0 sin cambios seguido de las variantes de los modelos nuevos."""
    extra = build_generated_offers(
        delivery_context=FIXTURE_DELIVERY_CONTEXT,
        observed_at=FIXTURE_NOW,
        expires_at=FIXTURE_EXPIRES_AT,
        snapshot_path=AMPLIADO_SNAPSHOT_PATH,
    )
    return [*fresh_p0_offers(), *extra]
