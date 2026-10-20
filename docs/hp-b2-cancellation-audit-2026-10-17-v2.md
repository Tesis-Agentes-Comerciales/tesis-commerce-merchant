# Merchant D17: auditoría HP/B2 — PARTIAL

Este documento referencia evidencia en un commit local real de Agent: `51fbaa339a8b025972804ff4c391263c42f31a00`. Ese commit todavía no se ha publicado; los enlaces siguientes serán accesibles en GitHub únicamente después de una publicación futura expresamente autorizada. No se ha inventado un SHA ni se afirma disponibilidad remota actual.

> **Actualización 2026-10-19.** La frase anterior era cierta el 18 de octubre, pero ya no lo es. `51fbaa3` está publicado: es la punta del PR #10 (draft) de `Tesis-Agentes-Comerciales/tesis-commerce-agent`, y los enlaces fijados a ese SHA ya son accesibles. Las correcciones del paquete se agregan en commits posteriores del mismo PR, en `adenda-2026-10-19.md`, junto al README. Esa adenda no modifica los archivos enlazados aquí. El manifiesto de `51fbaa3` conserva la huella citada abajo. La revisión del 2026-10-19 lo regenera solo para el README y la adenda.

La [evidencia canónica](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/README.md) describe una demostración experimental backend acotada; no una compra gobernada completa, UI humana real ni HNP con dos LLM reales.

## Incidencia histórica conservada

`AUDIT-MERCHANT-CANCEL-01`: `tests/integration/test_guarded_lookup.py::test_catalog_and_current_checkout_changes_preserve_history` ejecutó dos cancelaciones directas mediante `CheckoutService.cancel`, en las fases `guard_lookup_new` y `full` filtrada, cluster descartable `mer07a2-283ec644204b`, fixture `variant=B2`. `checkout_mutations` pasó 0→1→2. Hubo UPDATE de `checkout_sessions` y evento `checkout.canceled` según el código. Los UUID exactos no quedaron capturados en la evidencia sanitizada.

Las dos ejecuciones incumplieron el perímetro autorizado. No se observó continuación desde Agent/D2, pagos ni órdenes. El aislamiento no las vuelve conformes: el gate histórico permanece `NOT_APPROVED_CANCEL_INCIDENT_REQUIRES_HUMAN_REVIEW` y el test no se reclasifica como conforme.

### Cierre del incidente — actualización 2026-10-19

**Lo que se sabe.**

- El clúster `mer07a2-283ec644204b` era desechable: PostgreSQL 17.2 en loopback, dentro del área aislada de validación, detenido y con el puerto cerrado al terminar.
- El test es válido como test del producto. Llama a `cancel` a propósito, para comprobar que el lookup conserva la prueba de creación original después de cambiar el catálogo y el checkout actual.
- El fallo fue el método de selección. Se eligió por filtro de nombre (`-k`), y el nombre `preserve_history` no revela la llamada interna a `cancel`. Por eso corrió dos veces.

**Mitigación.** El Merchant PR #3 (`fix/merchant-ci-integration`) agrega el marker `mutates_checkout_state`. Está registrado en `pyproject.toml` con `--strict-markers`, y lo llevan los tests que actualizan, cancelan, completan o reescriben un checkout persistido, su Order o su evidencia de creación. `-m "not mutates_checkout_state"` los excluye. Verificación del 2026-10-19 sobre el árbol del PR #3 (`6126cb6`):

- `uv run pytest --collect-only -q -m "not mutates_checkout_state" -k preserve_history` da 0 seleccionados (401 deseleccionados).
- El mismo `-k` sin `-m` selecciona `tests/integration/test_guarded_lookup.py::test_catalog_and_current_checkout_changes_preserve_history`.
- 66 tests llevan el marker.

Solo se recolectó: no se ejecutaron tests ni se abrieron bases.

**Lo que no se puede verificar.** El estado exacto de ese clúster después de las dos cancelaciones (filas, UUID de run, actor y checkout) no está en la evidencia sanitizada. El clúster quedó en la máquina de Maldo, y esta revisión no lo reabrió ni lo consultó.

**Estado: cerrado con mitigación; revisión del clúster original no posible.** El cierre no aprueba el gate histórico, que sigue `NOT_APPROVED_CANCEL_INCIDENT_REQUIRES_HUMAN_REVIEW`, ni cambia el dictamen **PARTIAL**. La mitigación evita que se repita, pero no vuelve conformes las dos ejecuciones.

## Cobertura y límites

Merchant conserva 393 nodeids únicos: 249 ejecutados históricamente, incluido el test con incidencia, y 144 retenidos. No se suman repeticiones entre fases. El dictamen global sigue **PARTIAL**; I10/D08/D09 conservan cobertura parcial. D2 usa ASGI e identidad sintética, aunque su fuente D17 enlazada sí sea HTTP/PostgreSQL real.

Los siete `nodeid_sha256` cuyo identificador visible contiene `[REDACTED]` corresponden al original anterior a sanitización y no se recalculan desde la cadena sanitizada. En las filas D2, `commercial_posts=0` significa cero POST comerciales adicionales iniciados por D2, no ausencia de POST D17 previos.

> **Corrección/actualización 2026-10-19.** La adenda del paquete Agent (PR #10) hace tres precisiones:
>
> - El deny I05/I06 usó una política inyectada en tests. En el runtime de entonces enforce no podía activarse.
> - La variante local de I10 no tuvo test en el repositorio hasta el PR #15 del Agent.
> - La evidencia live se ejecutó una vez en Windows, con un worker de inyección de fallos que no está en ningún repositorio.
>
> Desde el Merchant PR #3, una historia de creación corrupta responde 500 `history_invalid`, ya no 503.

- [Auditoría completa](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/audit-report.md).
- [Cobertura y limitaciones](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/coverage-and-limitations.md).
- [Matriz de escenarios](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/scenario-matrix.csv).
- [Inventario Merchant](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/merchant-inventory-393.csv).
- [Manifiesto de publicación](https://github.com/Tesis-Agentes-Comerciales/tesis-commerce-agent/blob/51fbaa339a8b025972804ff4c391263c42f31a00/docs/b2-governance/hp-b2-backend-validation-2026-10-17-v2/hashes.sha256), SHA-256 `325942f68496104d519bebf03651e1b96dbb77d60827411625f11b88a0203441`.

Solo documentación. Sin nuevas pruebas comerciales, cambios de código, políticas, migraciones, dependencias o CI. Ningún enlace concede autorización de continuación, pago o publicación.
