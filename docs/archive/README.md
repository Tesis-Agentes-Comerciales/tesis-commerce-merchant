# Reportes de ejecución (históricos)

Reportes de los tickets Merchant de gobernanza B2, versionados el 15 y el 16 de octubre de 2026. Son
registros fechados: describen ramas, commits y corridas de ese momento, no el estado actual del
código, y no se reescriben.

Las corridas se hicieron en Windows con PostgreSQL 17.2 local, y algunos comandos están en
PowerShell. No son instrucciones vigentes: la verificación actual, incluida la suite de
integración contra un PostgreSQL desechable, está en el [README](../../README.md#verificación). El
runner Windows `tests/integration/guarded_cluster.py` que citan 07B-A y 07B-A2 se retiró.

| Reporte | Tema |
|---|---|
| [EC-MER-REC-01](ec-mer-rec-01-results.md) | Recuperación idempotente de creación ACP |
| [EC-GOV-06D-A / Merchant](ec-gov-06d-a-merchant-lookup-results.md) | Consulta autenticada de creación ACP |
| [EC-MER-07B-A](ec-mer-07b-a-d17-atomic-precondition-results.md) | Precondición atómica D17 |
| [EC-MER-07B-A2](ec-mer-07b-a2-readonly-guard-proof-results.md) | Lookup histórico D17 |

La auditoría HP/B2 y el incidente AUDIT-MERCHANT-CANCEL-01 siguen en
[docs/hp-b2-cancellation-audit-2026-10-17-v2.md](../hp-b2-cancellation-audit-2026-10-17-v2.md).
