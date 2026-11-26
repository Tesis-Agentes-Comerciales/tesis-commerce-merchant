# Perfil de protocolo

Qué parte de ACP y AP2 implementa este comercio y cómo maneja la moneda. Es un subconjunto
sintético hecho para la tesis: no afirma conformidad completa con ninguno de los dos protocolos y
nunca mueve dinero real.

## ACP

- **Versión y alcance.** ACP `2026-04-17`, transporte REST y solo el servicio de checkout. Los
  esquemas están congelados en [`vendor/acp/`](../vendor/acp/README.md) y el comercio valida
  contra ellos el discovery, el feed y las respuestas de checkout.
- **Discovery.** `GET /.well-known/acp.json` es público. El origen que anuncia sale de
  `ACP_API_BASE_URL` (`http://127.0.0.1:4120` en local); fuera de localhost tiene que ser HTTPS.
- **Catálogo.** `GET /feeds/current` publica el catálogo completo como Product Feed
  (`{metadata, products}`), leído en vivo de PostgreSQL: un cambio de precio o de stock se ve sin
  reexportar nada. Es una publicación propia de reemplazo completo, no la Feed API incremental de
  ACP. El agente consulta cada oferta con `POST /lab/offers/get`, una ruta propia con Bearer.
- **Checkout.** `/checkout_sessions` permite crear, leer, actualizar, cancelar y completar. Cada
  llamada lleva el Bearer sintético y la cabecera `API-Version`; las que tienen efecto exigen
  `Idempotency-Key`, y repetirlas no duplica nada. Cada sesión queda aislada por actor y corrida.
- **Estados.** Una sesión nace como `not_ready_for_payment` y pasa a `ready_for_payment` cuando
  tiene comprador, datos de entrega en Bogotá y la única opción de envío elegida. Actualizar solo
  acepta esos tres datos; cancelar acepta un cuerpo vacío o solo el motivo
  (`intent_trace.reason_code`).
- **Pago.** Hay un único handler de pago sintético, `tesis_sandbox`, con pago delegado: el agente
  obtiene un token opaco del proveedor sandbox (`tesis-payment-provider-sandbox`) y, al completar,
  el comercio se lo confirma al proveedor junto con el monto, la moneda y el checkout. Nunca deduce
  el resultado a partir del token. Las especificaciones del handler están en
  `/payment-handlers/tesis-sandbox/`.
- **Completar.** Si el proveedor aprueba, el comercio revalida los términos, descuenta el
  inventario y crea exactamente una orden en la misma transacción. Un rechazo es determinista y un
  error temporal del proveedor responde 503 sin efectos comerciales. `GET /orders/{order_id}`
  muestra la orden solo con datos saneados.
- **Webhooks.** La orden y su aviso `order_create` se guardan juntos en una cola de salida. El
  comercio no tiene un proceso propio que la vacíe: `python -m commerce_lab.db webhook-dispatch`
  entrega los avisos pendientes, firmados con `MERCHANT_WEBHOOK_SECRET`, a `WEBHOOK_RECEIVER_URL`,
  con hasta cinco intentos.
- **Errores.** Algunos códigos se publican en minúsculas, como `missing_api_version`,
  `out_of_stock` o `payment_declined`; los demás salen con su nombre interno en mayúsculas, como
  `INVALID_INPUT` o `CHECKOUT_NOT_FOUND`.

## AP2

AP2 (versión 0.2) se monta como un puente experimental sobre ACP. Sus rutas `/ap2/*` no son
estándar de ACP.

- **Con la persona presente.** `GET /checkout_sessions/{id}/ap2/checkout-jwt` entrega un JWT ES256,
  firmado por el comercio, con la instantánea completa de un checkout listo para pagar; dura cinco
  minutos. `GET /.well-known/ap2/jwks.json` publica la clave para verificarlo. Ambas rutas necesitan
  `MERCHANT_AP2_PRIVATE_KEY_PEM` y sin ella fallan cerrado.
- **Completar con mandato.** `POST /checkout_sessions/{id}/ap2/complete` verifica el mandato de
  checkout que firma el agente con su clave (`MERCHANT_AP2_AGENT_JWK_JSON`): firma, hash,
  instantánea y revisión. Si todo cuadra, completa la compra y devuelve un recibo firmado. Solo
  funciona con `MERCHANT_AP2_REQUIRED=true` y la clave del agente (sin ellas responde 503); en ese
  modo el `/complete` de ACP queda bloqueado y solo se compra por esta ruta.
- **Compra delegada, sin la persona presente.** El mandato llega como una cadena de dos partes,
  `<abierto>~~<cerrado>`. El comercio la verifica con la misma clave del agente y comprueba que él
  está entre los comercios permitidos (su identificador es `PAYMENT_MERCHANT_ID`) y que las líneas
  del checkout cumplen lo que el mandato autoriza. Si no cumplen, responde 422 con un recibo de
  error firmado; sin AP2 configurado, responde 503 `mandates_not_supported`. El perfil completo
  está en `docs/ap2-profile.md` del repositorio del agente.

## Moneda e importes

- Todos los importes son enteros en centavos; no hay `float` en ningún cálculo.
- El dominio, el checkout y el discovery usan `cop`; el esquema del feed exige `COP`. El exportador
  hace esa conversión de forma explícita.
- El precio del feed es solo el del producto, con impuestos incluidos: no hay motor fiscal. El
  envío es sintético y aparece aparte en el checkout: COP 25.000 por debajo de COP 2.000.000 y
  gratis desde ese valor.
- El feed es informativo. El checkout vuelve a leer la oferta en PostgreSQL y recalcula stock y
  totales. Crear un checkout pide una unidad, rechaza el stock cero y no reserva inventario; solo
  completar lo descuenta.

## Ofertas y variantes

Por dentro, cada oferta (`Offer`) es la instantánea de una variante: `Offer.id` es el id de la
variante y `Offer.product_id` el del producto que agrupa sus variantes en el feed. La oferta guarda
si el producto está activo; el feed comunica la disponibilidad en `Variant.availability`, porque el
esquema congelado de Product no tiene campo de estado. Las URLs `merchant.example.test` de las
pruebas son identificadores sintéticos, no páginas reales.
