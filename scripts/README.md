# Scripts

| Ruta | Uso |
|---|---|
| `railway_bootstrap.py` | Arranque del despliegue: el `Dockerfile` lo ejecuta antes de `uvicorn`. Crea `tesis_lab` si `BOOTSTRAP_ADMIN_DATABASE_URL` lo permite, aplica las migraciones, importa el surtido solo en un merchant vacío (P0, o el ampliado con `MERCHANT_CATALOG=ampliado`) y registra el hash de `MERCHANT_AGENT_BEARER_TOKEN` como sesión del agente (365 días) sin imprimirlo. Sin ese token omite la sesión. Ver [Despliegue de la demo sintética](../README.md#despliegue-de-la-demo-sintética). |
| [`catalog/`](catalog/README.md) | Recolectores de uso único que armaron los snapshots de los catálogos P0 y ampliado y las fotos de producto. |
