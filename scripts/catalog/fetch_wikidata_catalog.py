"""Recolecta un catálogo de productos reales desde Wikidata, de una sola pasada.

El resultado se commitea como snapshot (`src/commerce_lab/fixtures/catalog_snapshot.json`)
junto con su sha256. El fixture lee ese archivo, nunca la red: el catálogo sigue siendo
determinista y `verify()` puede comparar el snapshot sembrado byte a byte.

Las imágenes son de Wikimedia Commons, con licencias libres, así que se pueden publicar y
citar. Se guarda el nombre del archivo para poder atribuirlas.

Con `--ampliado` arma el catálogo ampliado, opcional y separado: suma marcas y gamas que el
surtido P0 no tiene, nunca repite un modelo de P0 y escribe su propio snapshot. El snapshot P0
es el contrato de las evaluaciones y este modo no lo lee para escribir, solo para excluir.

Uso:
    uv run python scripts/catalog/fetch_wikidata_catalog.py \
        --out src/commerce_lab/fixtures/catalog_snapshot.json
    uv run python scripts/catalog/fetch_wikidata_catalog.py --ampliado \
        --out src/commerce_lab/fixtures/catalog_snapshot_ampliado.json
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[2]
P0_SNAPSHOT: Final = ROOT / "src/commerce_lab/fixtures/catalog_snapshot.json"
ENDPOINT: Final = "https://query.wikidata.org/sparql"
COMMONS_API: Final = "https://commons.wikimedia.org/w/api.php"
USER_AGENT: Final = "TesisCommerceCatalog/1.0 (Uniandes thesis; academic use)"
QID: Final = re.compile(r"^Q\d+$")

# Clases de Wikidata que dan modelos de producto con imagen. Se usa "modelo de smartphone"
# y no "modelo de celular": la segunda arrastra plegables de los 2000 que no tienen lugar en
# una tienda actual.
FAMILIES: Final[tuple[tuple[str, str, str], ...]] = (("Q19723451", "smartphones", "Teléfono"),)

# El catálogo replica el surtido de una cadena como Best Buy: no vende Xiaomi, Huawei, Oppo
# ni vivo. La marca se compara ya normalizada.
STORE_BRANDS: Final[frozenset[str]] = frozenset(
    {"apple", "samsung", "google", "motorola", "oneplus", "tcl", "sony", "nokia"}
)
# Series que la cadena efectivamente ofrece hoy.
STORE_SERIES: Final = re.compile(
    r"(iphone|galaxy|pixel|moto\s*g|motorola\s*edge|razr|oneplus|nord|xperia)", re.I
)

# Un producto entra al catálogo si su fecha de lanzamiento es reciente o si su nombre
# pertenece a una serie vigente. La segunda regla existe porque solo uno de cada seis
# modelos trae fecha en Wikidata, y sin ella se perderían casi todos los equipos actuales.
MODERN_FROM: Final = 2016
MODERN_SERIES: Final = re.compile(
    r"""(
     iphone\s*(1[1-9]|se)| galaxy\s*(s(1[0-9]|2[0-9])|z\s*(fold|flip)|a[0-9]{2}|m[0-9]{2}
      |note\s*(1[0-9]|2[0-9]))| pixel\s*([6-9]|1[0-9])| redmi\s*(note\s*)?(9|1[0-9]|k[0-9]{2})|
     poco\s*[fxm][0-9]| (mi|xiaomi)\s*1[0-4]| oneplus\s*([7-9]|1[0-9]|nord)| mate\s*[2-6][0-9]|
     moto\s*g[0-9]{2}| edge\s*[0-9]{2}| reno\s*[0-9]| find\s*x[0-9]| vivo\s*[xyv][0-9]{2}|
     realme\s*([7-9]|1[0-9])| zenfone\s*[6-9]| rog\s*phone| xperia\s*(1|5|10)\s*i
    )""",
    re.I | re.X,
)


_CORPORATE: Final = re.compile(
    r"\s*(Inc\.|Corporation|Electronics|Group|Co\., Ltd\.|Mobile Communications"
    r"|Technologies|LLC|Mobility).*$"
)
_TITLE_BRANDS: Final[dict[str, str]] = {
    "iphone": "Apple",
    "galaxy": "Samsung",
    "pixel": "Google",
    "moto": "Motorola",
    "razr": "Motorola",
    "nord": "OnePlus",
    "xperia": "Sony",
}


def _normalized_brand(brand: str | None, label: str) -> str:
    """Nombre comercial de la marca; Wikidata devuelve razones sociales o nada."""
    if brand:
        cleaned = _CORPORATE.sub("", brand).strip()
        if cleaned:
            return cleaned
    head = label.split()[0].casefold()
    return _TITLE_BRANDS.get(head, label.split()[0])


def _is_modern(label: str, released: str | None) -> bool:
    if released and released[:4].isdigit():
        return int(released[:4]) >= MODERN_FROM
    return bool(MODERN_SERIES.search(label))


QUERY: Final = """
SELECT ?item ?itemLabel (SAMPLE(?brandLabel) AS ?brand) (MIN(?date) AS ?released)
       (SAMPLE(?img) AS ?image) WHERE {
  ?item wdt:P31/wdt:P279* wd:%s .
  ?item wdt:P18 ?img .
  OPTIONAL { ?item wdt:P176 ?brandItem .
             ?brandItem rdfs:label ?brandLabel . FILTER(LANG(?brandLabel) = "en") }
  OPTIONAL { ?item wdt:P577 ?date }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
GROUP BY ?item ?itemLabel
LIMIT 1500
"""


def _sparql(query: str) -> list[dict[str, Any]]:
    url = f"{ENDPOINT}?{urllib.parse.urlencode({'query': query})}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": USER_AGENT,
            # Sin esto la respuesta llega comprimida y `read()` puede cortarla a la mitad.
            "Accept-Encoding": "identity",
        },
    )
    # Wikidata corta la respuesta cuando limita al cliente; se reintenta con espera.
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                body = response.read()
            payload = json.loads(body.decode("utf-8"))
            return payload["results"]["bindings"]
        except (json.JSONDecodeError, OSError) as error:
            last_error = error
            wait = 10 * (attempt + 1)
            print(f"    respuesta incompleta, reintento en {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("Wikidata no devolvió una respuesta completa") from last_error


def _value(row: dict[str, Any], key: str) -> str | None:
    cell = row.get(key)
    if not cell:
        return None
    value = str(cell.get("value", "")).strip()
    return value or None


def _commons_file(image_url: str) -> str | None:
    """`Special:FilePath/Foo.jpg` -> `Foo.jpg`, ya decodificado."""
    marker = "/Special:FilePath/"
    if marker not in image_url:
        return None
    return urllib.parse.unquote(image_url.split(marker, 1)[1])


def collect() -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for class_id, category, kind in FAMILIES:
        rows = _sparql(QUERY % class_id)
        print(f"  {class_id} ({category}): {len(rows)} filas", flush=True)
        for row in rows:
            item = _value(row, "item")
            label = _value(row, "itemLabel")
            image = _value(row, "image")
            if not item or not label or not image:
                continue
            # Sin etiqueta traducida Wikidata devuelve el propio Q-id: no sirve como título.
            if QID.match(label):
                continue
            file_name = _commons_file(image)
            if not file_name:
                continue
            entity = item.rsplit("/", 1)[-1]
            if entity in seen:
                continue
            brand = _value(row, "brand")
            if brand and QID.match(brand):
                brand = None
            released = _value(row, "released")
            released = _value(row, "released")
            released_year = released[:10] if released else None
            if not _is_modern(label, released_year):
                continue
            normalized = _normalized_brand(brand, label)
            if normalized.casefold() not in STORE_BRANDS or not STORE_SERIES.search(label):
                continue
            seen[entity] = {
                "entity": entity,
                "title": label,
                "brand": normalized,
                "category": category,
                "kind": kind,
                "released": released_year,
                "image_file": file_name,
                # Las fotos reales se prefieren sobre el arte vectorial al ordenar.
                "is_vector": file_name.lower().endswith((".svg", ".svgz")),
            }
        time.sleep(1)
    return sorted(seen.values(), key=lambda row: (row["category"], row["title"], row["entity"]))


# --- Catálogo ampliado ------------------------------------------------------------------------

# Misma clase que P0 más los modelos que Wikidata declara como "smartphone" a secas. La etiqueta
# admite otros idiomas porque varios modelos recientes no tienen etiqueta en inglés; la fecha de
# publicación (P577) se completa con la de creación (P571) cuando falta.
AMPLIADO_QUERY: Final = """
SELECT ?item ?itemLabel (SAMPLE(?brandLabel) AS ?brand) (MIN(?date) AS ?released)
       (SAMPLE(?img) AS ?image) WHERE {
  { ?item wdt:P31/wdt:P279* wd:Q19723451 . } UNION { ?item wdt:P31 wd:Q22645 . }
  ?item wdt:P18 ?img .
  OPTIONAL { ?item wdt:P176 ?brandItem .
             ?brandItem rdfs:label ?brandLabel . FILTER(LANG(?brandLabel) = "en") }
  OPTIONAL { ?item wdt:P577|wdt:P571 ?date }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul,es,fr,de,it,pt,nl". }
}
GROUP BY ?item ?itemLabel
LIMIT 6000
"""

# Marcas de una tienda colombiana de telefonía. Prioriza las que P0 no tiene; Apple queda
# fuera porque P0 ya cubre su línea completa y Wikidata solo la ilustra con vectores.
AMPLIADO_BRANDS: Final[frozenset[str]] = frozenset(
    {
        "Xiaomi",
        "Huawei",
        "Honor",
        "Oppo",
        "Realme",
        "Vivo",
        "Nokia",
        "ASUS",
        "Nothing",
        "Motorola",
        "OnePlus",
        "Tecno",
        "ZTE",
        "Samsung",
        "Sony",
        "Google",
    }
)
# La primera palabra del título nombra la marca comercial mejor que el fabricante declarado:
# Redmi y POCO se venden como Xiaomi, Honor ya no es Huawei y los Nokia recientes los fabrica HMD.
AMPLIADO_TITLE_BRANDS: Final[dict[str, str]] = {
    "redmi": "Xiaomi",
    "poco": "Xiaomi",
    "pocophone": "Xiaomi",
    "xiaomi": "Xiaomi",
    "mi": "Xiaomi",
    "black": "Xiaomi",
    "huawei": "Huawei",
    "honor": "Honor",
    "oppo": "Oppo",
    "realme": "Realme",
    "vivo": "Vivo",
    "nokia": "Nokia",
    "asus": "ASUS",
    "zenfone": "ASUS",
    "rog": "ASUS",
    "nothing": "Nothing",
    "cmf": "Nothing",
    "moto": "Motorola",
    "motorola": "Motorola",
    "razr": "Motorola",
    "oneplus": "OnePlus",
    "samsung": "Samsung",
    "galaxy": "Samsung",
    "google": "Google",
    "pixel": "Google",
    "sony": "Sony",
    "xperia": "Sony",
    "iphone": "Apple",
    "tecno": "Tecno",
    "zte": "ZTE",
}

# Un modelo con fecha entra desde este año. Sin fecha, solo si su serie es reciente: la regla de
# P0 (MODERN_SERIES) deja pasar líneas antiguas de estas marcas, como Honor 6 o Nokia C5-00.
AMPLIADO_FROM: Final = 2017
AMPLIADO_SERIES: Final = re.compile(
    r"""(
     redmi\s*(note\s*)?(8|9|1[0-9])| redmi\s*(k[2-9]0|a[1-9]|turbo|s2)| poco| pocophone|
     xiaomi\s*(1[1-9]|civi|mix\s*fold)| (xiaomi\s*)?mi\s*(8|9|1[01]|a[23])\b| black\s*shark|
     huawei\s*(p[2-6]0|mate\s*([2-8]0|x)|nova|y[5-9]\s*\(?20(1[89]|2)|p\s*smart)|
     honor\s*(8x|9|x[0-9]|play|gt|magic|p\s*smart|[1-9][0-9]{1,2}\b)|
     oppo\s*(find\s*[xn]|reno|a[0-9]{2}|f(9|1[0-9])|r1[5-9])| realme| vivo\s*[xyvt][0-9]|
     nokia\s*([1-9]\.[1-4]|[1-9](\s*plus)?$|c[1-9][0-9]?$|g[0-9]{2}|xr?[0-9]{2}|9\s*pureview)|
     zenfone\s*([7-9]|1[0-9]|max\s*\(?m|5\s*\(2018)| rog\s*phone| nothing\s*phone| cmf\s*phone|
     moto\s*(g[5-9]|g[0-9]{2}|e[5-9]|z[2-4]|x4|c\b)| edge\s*[0-9]{2}| motorola\s*edge|
     oneplus\s*([5-9]|1[0-9]|open|nord)|
     galaxy\s*(a[0-9]{2}|a8\s*(\(2018\)|star)|a8s|m[0-9]{2}|f[0-9]{2}|j[468]\+?$|j[57]\s*\(2017\)
       |xcover\s*[4-9]|z\s*(fold|flip|trifold)|fold|s(9|1[0-9]|2[0-9])|note\s*(9|1[0-9]))|
     xperia\s*((1|5|10)\s*[ivx]+|xz[1-3]|xa2)| pixel\s*([3-9]|1[0-9])|
     tecno\s*(camon\s*(1[2-9]|[2-9][0-9])|spark\s*([4-9]|[1-9][0-9])|pova|phantom)|
     axon\s*[1-9][0-9]| blade\s*[av][0-9]
    )""",
    re.I | re.X,
)

# Descartes por revisión visual de la foto en Commons. Cada uno lleva su motivo: la tienda solo
# muestra fotos donde el modelo es el sujeto, no vitrinas, capturas de pantalla ni dibujos.
AMPLIADO_EXCLUDED: Final[dict[str, str]] = {
    "Q25403833": "la foto es del A59 de 2016 en una vitrina",
    "Q28845516": "el teléfono ocupa poco de la foto",
    "Q29197075": "muestra el Galaxy S8 y el S8+ juntos",
    "Q51845248": "la foto es del P20 Pro",
    "Q55230221": "foto del ROG Phone 2 en una exhibición",
    "Q56401623": "exhibición; los teléfonos son pequeños",
    "Q56731673": "dibujo plano, no foto",
    "Q56755071": "silueta vectorial, no foto",
    "Q58638342": "la foto es del Honor X9b",
    "Q59357246": "la foto es del R17 Pro",
    "Q61369883": "el vidrio de la pantalla está roto",
    "Q61721774": "vitrina con el cartel del Mi 8",
    "Q62063540": "la foto es del Moto G7 Plus",
    "Q64549901": "vitrina con el 14T y el 14T Pro",
    "Q64550623": "evento de lanzamiento con público",
    "Q81198403": "la foto es de un teléfono básico, no del Nokia C1",
    "Q84368489": "el teléfono ocupa poco de la foto",
    "Q85805417": "la foto es del Camon 12 Air",
    "Q86258678": "captura de pantalla, no foto",
    "Q97162239": "vitrina con precio; el teléfono es pequeño",
    "Q97201913": "vitrina con dos teléfonos",
    "Q97380020": "pantalla rayada, recorte de baja calidad",
    "Q98301286": "la pantalla muestra un anuncio de otra marca",
    "Q105763874": "la foto es del Redmi Note 10S",
    "Q106504943": "dibujo plano, no foto",
    "Q107135797": "solo muestra la cámara",
    "Q109264525": "vitrina con precio",
    "Q109264715": "vitrina con precio; el teléfono es pequeño",
    "Q109264718": "vitrina con carteles de agotado",
    "Q110159605": "vitrina con precio",
    "Q111477320": "retrato de una persona; el teléfono es secundario",
    "Q113043450": "vitrina con carteles de precio",
    "Q119788606": "el cartel de precios ocupa media foto",
    "Q122046850": "la pantalla muestra que es el Mate 60 Pro",
    "Q122356231": "vitrina con precio",
    "Q124799123": "exhibición de tienda; el teléfono es diminuto",
    "Q130272078": "vitrina de cristal; el teléfono es pequeño",
    "Q131345481": "exhibición con varios modelos",
    "Q131369746": "la pantalla muestra que es el Xiaomi 15 Pro",
    "Q133828220": "el vidrio de la cámara está roto",
    "Q135543826": "muestra dos modelos de la serie 13T",
    "Q135552065": "exhibición de tienda; el teléfono es pequeño",
    "Q136718376": "exhibición de tienda; el teléfono es pequeño",
    "Q137382448": "la foto es del Mate 80 Pro Max",
    "Q140690372": "exhibición con tres modelos",
}

# Modelos que la regla de gama del generador, compartida con P0, deja con un precio absurdo para
# su segmento: «Plus», «Pro» o «Max» suben a gama alta teléfonos de entrada o media, y los
# plegables o gama alta recientes sin esas palabras caen a gama media. Como `generated.py` no se
# toca, el modelo entero se excluye y ninguna otra entidad con el mismo nombre ocupa su lugar.
AMPLIADO_MISPRICED: Final[dict[str, str]] = {
    "Q55694209": "Nokia 5.1 Plus, gama media a 3,2 M",
    "Q65042230": "Nokia 1 Plus, gama de entrada a 4,3 M",
    "Q99701353": "Oppo F11 Pro, gama media a 4 M",
    "Q101244860": "Huawei Mate Xs, plegable a 1 M",
    "Q109264526": "Redmi Note 10 Pro, gama media a 3,1 M",
    "Q112075188": "Redmi Note 11 Pro, gama media a 3,1 M",
    "Q117280924": "POCO X3 Pro, gama media a 4,1 M",
    "Q118288605": "Sony Xperia 1 V, gama alta de 2023 a 1,2 M",
    "Q122204657": "Sony Xperia 5 V, gama alta de 2023 a 1,1 M",
    "Q123232418": "OnePlus Open, plegable de 2023 a 1 M",
    "Q124485887": "Oppo Find X7, gama alta de 2024 a 1 M",
    "Q127872524": "Vivo X100, gama alta de 2023 a 1,2 M",
    "Q130321406": "ASUS ZenFone Max (M2), gama de entrada a 4,9 M",
    "Q131349208": "Oppo Find X8, gama alta de 2024 a 0,8 M",
    "Q134464097": "CMF Phone 2 Pro, gama de entrada a 3,5 M",
    "Q135189882": "Nothing Phone 3, gama alta de 2025 a 1 M",
    "Q136398996": "Xiaomi 17, gama alta de 2025 a 0,9 M",
    "Q137208843": "Samsung Galaxy Z TriFold, plegable triple a 1,8 M",
}

# Licencias libres que publica Commons en `LicenseShortName`. La coincidencia es completa, así que
# las variantes NC o ND (uso no comercial, sin derivadas) nunca pasan.
FREE_LICENSE: Final = re.compile(
    r"^(?!.*\bn[cd]\b)(cc0( 1\.0)?|cc[ -]by(-sa)?[ -][0-9.]+( [a-z]{2,3})?"
    r"|public domain|gfdl( [0-9.]+)?|attribution)$",
    re.I,
)


def _title_brand(label: str, fallback: str | None) -> str:
    head = re.split(r"[\s(]", label.strip(), maxsplit=1)[0].casefold()
    return AMPLIADO_TITLE_BRANDS.get(head) or _normalized_brand(fallback, label)


# Mayúsculas de la marca como las escribe la propia marca y P0.
AMPLIADO_TITLE_HEADS: Final[dict[str, str]] = {"asus": "ASUS", "poco": "POCO", "realme": "Realme"}


def _ampliado_title(label: str) -> str:
    """Título de tienda: Honor sin el «Huawei» de antes de 2020 y la marca con sus mayúsculas."""
    title = re.sub(r"^huawei\s+(?=honor\b)", "", label.strip(), flags=re.I)
    head, separator, rest = title.partition(" ")
    return AMPLIADO_TITLE_HEADS.get(head.casefold(), head) + separator + rest


def _model_key(brand: str, title: str) -> tuple[str, str]:
    """Clave de modelo para no repetir uno de P0 con otro identificador o sin la marca."""
    noise = {brand.casefold(), "5g", "4g", "smartphone", *(b.casefold() for b in AMPLIADO_BRANDS)}
    tokens = [token for token in re.findall(r"[a-z0-9+]+", title.casefold()) if token not in noise]
    return brand, " ".join(tokens)


def _plain(value: str) -> str:
    """Texto de un campo HTML de Commons, en una línea y sin romper la tabla de procedencia."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", text).replace("|", "/").strip()[:120]


def _commons_metadata(files: list[str]) -> dict[str, dict[str, str]]:
    """Licencia, autor y tipo de cada archivo de Commons, en lotes de cincuenta."""
    metadata: dict[str, dict[str, str]] = {}
    for start in range(0, len(files), 50):
        batch = files[start : start + 50]
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "imageinfo",
            "iiprop": "extmetadata|mime",
            "iiextmetadatafilter": "LicenseShortName|Artist|NonFree",
            "titles": "|".join(f"File:{name}" for name in batch),
        }
        request = urllib.request.Request(
            f"{COMMONS_API}?{urllib.parse.urlencode(params)}",
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            query = json.loads(response.read().decode("utf-8"))["query"]
        # Commons normaliza el título pedido (mayúscula inicial, espacios); se vuelve al original.
        original = {row["to"]: row["from"] for row in query.get("normalized", [])}
        for page in query.get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            fields = info.get("extmetadata") or {}
            name = original.get(page["title"], page["title"]).removeprefix("File:")
            metadata[name] = {
                "license": _plain(str(fields.get("LicenseShortName", {}).get("value", ""))),
                "author": _plain(str(fields.get("Artist", {}).get("value", ""))),
                "nonfree": str(fields.get("NonFree", {}).get("value", "")).casefold(),
                "mime": str(info.get("mime", "")),
            }
        time.sleep(1)
    return metadata


def collect_ampliado() -> list[dict[str, Any]]:
    p0_products = json.loads(P0_SNAPSHOT.read_text(encoding="utf-8"))["products"]
    p0_entities = {str(row["entity"]) for row in p0_products}
    p0_models = {
        _model_key(_title_brand(str(row["title"]), str(row["brand"])), str(row["title"]))
        for row in p0_products
    }
    rows = _sparql(AMPLIADO_QUERY)
    print(f"  smartphones con imagen: {len(rows)} filas", flush=True)

    candidates: list[dict[str, Any]] = []
    for row in rows:
        item, label, image = _value(row, "item"), _value(row, "itemLabel"), _value(row, "image")
        if not item or not label or not image or QID.match(label):
            continue
        entity = item.rsplit("/", 1)[-1]
        file_name = _commons_file(image)
        if entity in p0_entities or entity in AMPLIADO_EXCLUDED or not file_name:
            continue
        if file_name.lower().endswith((".svg", ".svgz")):
            continue
        title = _ampliado_title(label)
        maker = _value(row, "brand")
        brand = _title_brand(title, None if maker and QID.match(maker) else maker)
        released = _value(row, "released")
        released_day = released[:10] if released and released[:4].isdigit() else None
        if brand not in AMPLIADO_BRANDS:
            continue
        if released_day:
            if int(released_day[:4]) < AMPLIADO_FROM:
                continue
        elif not AMPLIADO_SERIES.search(title):
            continue
        candidates.append(
            {
                "entity": entity,
                "title": title,
                "brand": brand,
                "category": "smartphones",
                "kind": "Teléfono",
                "released": released_day,
                "image_file": file_name,
                "is_vector": False,
            }
        )

    # Primero los más recientes: si dos entidades nombran el mismo modelo, queda la fechada.
    candidates.sort(
        key=lambda row: (-int((row["released"] or "0")[:4]), row["title"], row["entity"])
    )
    selected: list[dict[str, Any]] = []
    models = set(p0_models)
    for row in candidates:
        key = _model_key(row["brand"], row["title"])
        if key in models:
            continue
        models.add(key)
        if row["entity"] not in AMPLIADO_MISPRICED:
            selected.append(row)

    licenses = _commons_metadata(sorted({row["image_file"] for row in selected}))
    usable: list[dict[str, Any]] = []
    for row in selected:
        meta = licenses.get(row["image_file"])
        if meta is None or meta["nonfree"] in {"1", "true"}:
            print(f"    sin licencia libre: {row['entity']} {row['image_file']}", flush=True)
            continue
        if not FREE_LICENSE.match(meta["license"]) or meta["mime"] not in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }:
            print(f"    descartada: {row['entity']} {meta['license']} {meta['mime']}", flush=True)
            continue
        usable.append({**row, "image_license": meta["license"], "image_author": meta["author"]})
    return sorted(usable, key=lambda row: (row["category"], row["title"], row["entity"]))


def main() -> None:
    parser = argparse.ArgumentParser(prog="fetch_wikidata_catalog")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ampliado", action="store_true")
    arguments = parser.parse_args()
    if arguments.ampliado and arguments.out.resolve() == P0_SNAPSHOT:
        parser.error("el catálogo ampliado no puede reescribir el snapshot P0")

    print("Consultando Wikidata...", flush=True)
    products = collect_ampliado() if arguments.ampliado else collect()
    document = {
        "source": "Wikidata (https://www.wikidata.org), imágenes de Wikimedia Commons",
        "license": "Datos: CC0. Imágenes: licencias libres por archivo, ver image_file.",
        "product_count": len(products),
        "products": products,
    }
    body = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    arguments.out.with_suffix(".sha256").write_text(digest + "\n", encoding="utf-8")

    by_category: dict[str, int] = {}
    by_brand: dict[str, int] = {}
    for product in products:
        by_category[product["category"]] = by_category.get(product["category"], 0) + 1
        by_brand[product["brand"]] = by_brand.get(product["brand"], 0) + 1
    summary = {"total": len(products), "por_categoria": by_category, "sha256": digest[:16]}
    if arguments.ampliado:
        summary["por_marca"] = dict(sorted(by_brand.items()))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
