"""El catálogo ampliado es opcional y P0, el contrato de las evaluaciones, no cambia."""

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from commerce_lab import managed_catalog
from commerce_lab.db.core import FIXTURE_VERSION, fixture_snapshot
from commerce_lab.feed import build_feed
from commerce_lab.fixtures import fresh_p0_offers
from commerce_lab.fixtures.ampliado import AMPLIADO_SNAPSHOT_PATH, ampliado_offers
from commerce_lab.fixtures.generated import SNAPSHOT_PATH

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets/products"
BUNDLE = json.loads((ROOT / "vendor/acp/2026-04-17/schema.feed.json").read_text(encoding="utf-8"))
REGISTRY = Registry().with_resource(str(BUNDLE["$id"]), Resource.from_contents(BUNDLE))
ORIGIN = "https://merchant.example.test"

# Identidad del fixture P0 que siembran las evaluaciones. Es un contrato congelado: si alguno de
# estos valores cambia, cambiaron las respuestas correctas de los casos, y eso exige subir
# FIXTURE_VERSION y volver a fijarlos, nunca solo actualizar el número.
P0_FIXTURE_SHA256 = "dabce996e2ee6431d377a25614bfe9df849d2675f33a5d28bc61f3aec7579e64"
P0_SNAPSHOT_SHA256 = "724ab9ad656946924d06d75787cd094047b1b718960d804b0749c4ce45793815"
P0_OFFER_COUNT = 310
P0_PRODUCT_COUNT = 141
# (precio del ítem, stock, color, plazo) de variantes que los casos usan como referencia.
P0_VARIANTS = {
    "Q100286751-256GB": (1_310_000_00, 11, "grafito", 4),
    "Q106629718-128GB": (1_340_000_00, 1, "azul", 2),
    "Q108044294-512GB": (1_290_000_00, 0, "azul", 4),
    "BBGooglePixel8-128GB": (1_540_000_00, 13, "negro", 6),
}
NEW_BRANDS = {"Xiaomi", "Huawei", "Honor", "Oppo", "Realme", "Vivo", "Nokia", "ASUS", "Nothing"}
# Coincidencia completa: una variante NC o ND no pasa.
FREE_LICENSE = re.compile(
    r"^(?!.*\bn[cd]\b)(cc0( 1\.0)?|cc[ -]by(-sa)?[ -][0-9.]+( [a-z]{2,3})?"
    r"|public domain|gfdl( [0-9.]+)?|attribution)$",
    re.I,
)


def ampliado_snapshot() -> list[dict[str, Any]]:
    return json.loads(AMPLIADO_SNAPSHOT_PATH.read_text(encoding="utf-8"))["products"]


def ampliado_provenance() -> dict[str, str]:
    """Archivo servido -> archivo en Commons, según la sección del catálogo ampliado."""
    text = (ASSETS / "PROVENANCE.md").read_text(encoding="utf-8")
    section = text.split("\n## Catálogo ampliado\n", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `([^`]+)` \| [^|]+ \| `([^`]+)` \|", section, re.M)
    return dict(rows)


def validate(definition: str, value: object) -> None:
    Draft202012Validator(
        {"$ref": f"{BUNDLE['$id']}#/$defs/{definition}"},
        registry=REGISTRY,
        format_checker=FormatChecker(),
    ).validate(value)


def test_p0_fixture_stays_the_evaluation_contract() -> None:
    offers, digest, manifest = fixture_snapshot()

    assert FIXTURE_VERSION == "p0-catalog-v13"
    assert digest == P0_FIXTURE_SHA256
    assert manifest == {
        "fixture_sha256": P0_FIXTURE_SHA256,
        "hash_format": "sorted-object-keys-json-v1",
        "offer_count": P0_OFFER_COUNT,
    }
    assert len({offer["product_id"] for offer in offers}) == P0_PRODUCT_COUNT
    assert hashlib.sha256(SNAPSHOT_PATH.read_bytes()).hexdigest() == P0_SNAPSHOT_SHA256
    by_id = {offer["id"]: offer for offer in offers}
    for variant_id, (price, stock, color, days) in P0_VARIANTS.items():
        offer = by_id[variant_id]
        assert offer["pricing"]["items_total_minor"] == price
        assert offer["available_quantity"] == stock
        assert offer["color"] == color
        assert offer["delivery_days"] == days
    new_entities = {product["entity"] for product in ampliado_snapshot()}
    assert not {offer["id"].rsplit("-", 1)[0] for offer in offers} & new_entities


def test_ampliado_is_p0_followed_by_new_models() -> None:
    p0 = fresh_p0_offers()
    offers = ampliado_offers()
    new = offers[len(p0) :]

    assert offers[: len(p0)] == p0
    assert len({offer.id for offer in offers}) == len(offers)
    new_products = {offer.product_id for offer in new}
    assert not new_products & {offer.product_id for offer in p0}
    # Cada modelo del snapshot llega al catálogo: ninguno se queda sin foto.
    assert len(new_products) == len(ampliado_snapshot()) >= 100
    assert {offer.brand for offer in new} >= NEW_BRANDS
    assert {offer.category for offer in new} == {"smartphones"}
    assert all(offer.image_url and offer.image_url.startswith("/assets/products/") for offer in new)


def test_ampliado_snapshot_has_unique_models_with_free_photos_and_provenance() -> None:
    products = ampliado_snapshot()
    p0_products = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["products"]
    provenance = ampliado_provenance()
    body = AMPLIADO_SNAPSHOT_PATH.read_bytes()

    sidecar = AMPLIADO_SNAPSHOT_PATH.with_suffix(".sha256").read_text(encoding="utf-8")
    assert sidecar.strip() == hashlib.sha256(body).hexdigest()
    entities = [product["entity"] for product in products]
    titles = [product["title"].casefold() for product in products]
    assert len(set(entities)) == len(entities)
    assert len(set(titles)) == len(titles)
    assert not set(entities) & {product["entity"] for product in p0_products}
    assert not set(titles) & {product["title"].casefold() for product in p0_products}
    assert len(provenance) == len(products)
    for product in products:
        files = list(ASSETS.glob(f"{product['entity']}.*"))
        assert len(files) == 1, product["entity"]
        assert files[0].suffix in {".jpg", ".png", ".webp"}
        assert files[0].stat().st_size > 2_000
        assert not product["is_vector"]
        assert not product["image_file"].lower().endswith((".svg", ".svgz"))
        assert provenance[files[0].name] == product["image_file"]
        assert FREE_LICENSE.match(product["image_license"]), product["entity"]
        # La marca del título se escribe como la marca, y Honor ya no lleva «Huawei».
        head = product["title"].split()[0]
        assert head.casefold() != product["brand"].casefold() or head == product["brand"]
        assert not product["title"].casefold().startswith("huawei honor"), product["entity"]


def test_ampliado_feed_is_schema_valid_and_serves_every_photo() -> None:
    offers = ampliado_offers()
    metadata, products = build_feed(offers, base_url=ORIGIN)

    validate("FeedMetadata", metadata)
    assert len(products) == P0_PRODUCT_COUNT + len(ampliado_snapshot())
    variants = [
        variant
        for product in products
        for variant in cast(list[dict[str, Any]], product["variants"])
    ]
    assert {variant["id"] for variant in variants} == {offer.id for offer in offers}
    for product in products:
        validate("Product", product)
        url = str(cast(list[dict[str, Any]], product["media"])[0]["url"])
        assert url.startswith(f"{ORIGIN}/assets/products/")
        assert (ASSETS / url.rsplit("/", 1)[1]).is_file()


def test_import_current_only_takes_ampliado_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_import(catalog: str = "p0") -> dict[str, object]:
        calls.append(catalog)
        return {"status": "imported"}

    monkeypatch.setattr(managed_catalog, "import_current_catalog", fake_import)
    for argv in (["import-current"], ["import-current", "--catalog", "ampliado"]):
        monkeypatch.setattr(sys, "argv", ["managed_catalog", *argv])
        managed_catalog.main()

    assert calls == ["p0", "ampliado"]


def test_import_current_rejects_an_unknown_catalog_before_connecting() -> None:
    with pytest.raises(ValueError):
        managed_catalog.import_current_catalog("todo")  # type: ignore[arg-type]
