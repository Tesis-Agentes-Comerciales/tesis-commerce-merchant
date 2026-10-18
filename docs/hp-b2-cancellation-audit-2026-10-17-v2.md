# Merchant D17: auditoría HP/B2 — PARTIAL

Este documento referencia evidencia en un commit local real de Agent: `51fbaa339a8b025972804ff4c391263c42f31a00`. Ese commit todavía no se ha publicado; los enlaces siguientes serán accesibles en GitHub únicamente después de una publicación futura expresamente autorizada. No se ha inventado un SHA ni se afirma disponibilidad remota actual.

La [evidencia canónica](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/README.md) describe una demostración experimental backend acotada; no una compra gobernada completa, UI humana real ni HNP con dos LLM reales.

## Incidencia histórica conservada

`AUDIT-MERCHANT-CANCEL-01`: `tests/integration/test_guarded_lookup.py::test_catalog_and_current_checkout_changes_preserve_history` ejecutó dos cancelaciones directas mediante `CheckoutService.cancel`, en las fases `guard_lookup_new` y `full` filtrada, cluster descartable `mer07a2-283ec644204b`, fixture `variant=B2`. `checkout_mutations` pasó 0→1→2. Hubo UPDATE de `checkout_sessions` y evento `checkout.canceled` según el código. Los UUID exactos no quedaron capturados en la evidencia sanitizada.

Las dos ejecuciones incumplieron el perímetro autorizado. No se observó continuación desde Agent/D2, pagos ni órdenes. El aislamiento no las vuelve conformes: el gate histórico permanece `NOT_APPROVED_CANCEL_INCIDENT_REQUIRES_HUMAN_REVIEW` y el test no se reclasifica como conforme.

## Cobertura y límites

Merchant conserva 393 nodeids únicos: 249 ejecutados históricamente, incluido el test con incidencia, y 144 retenidos. No se suman repeticiones entre fases. El dictamen global sigue **PARTIAL**; I10/D08/D09 conservan cobertura parcial. D2 usa ASGI e identidad sintética, aunque su fuente D17 enlazada sí sea HTTP/PostgreSQL real.

Los siete `nodeid_sha256` cuyo identificador visible contiene `[REDACTED]` corresponden al original anterior a sanitización y no se recalculan desde la cadena sanitizada. En las filas D2, `commercial_posts=0` significa cero POST comerciales adicionales iniciados por D2, no ausencia de POST D17 previos.

- [Auditoría completa](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/audit-report.md).
- [Cobertura y limitaciones](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/coverage-and-limitations.md).
- [Matriz de escenarios](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/scenario-matrix.csv).
- [Inventario Merchant](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/merchant-inventory-393.csv).
- [Manifiesto de publicación](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/hashes.sha256), SHA-256 `325942f68496104d519bebf03651e1b96dbb77d60827411625f11b88a0203441`.

Solo documentación. Sin nuevas pruebas comerciales, cambios de código, políticas, migraciones, dependencias o CI. Ningún enlace concede autorización de continuación, pago o publicación.
