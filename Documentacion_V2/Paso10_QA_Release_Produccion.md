# Paso10 - QA, release y produccion

Estado: **No-Go, bloqueado por ops** (actualizado 2026-09-30). Falta Infisical `prod`, VPS, Clerk prod y la QA manual con sesion real.

Objetivo: cerrar una version publicable con pruebas automaticas, manuales y operativas, y dejar constancia firmada de cada release.

**Las tareas ejecutables estan solo en `PasosParaProduccion.md`** (checklist unica, en orden). Este fichero no las repite: guarda el alcance, las decisiones Go/No-Go y el registro de releases.

## Alcance del primer go-live (producto minimo)

Soft launch con invitados, plan Basico como referencia: documentos, knowledge y chat. Planes asignados por el SADM en `/sadm/plans`.

Fuera de alcance (ver "Aplazado" en `PasosParaProduccion.md`): staging, cobro de planes (Stripe retirado, D016), WhatsApp/Telegram, Google Calendar y voz (D012), Langfuse prod.

## Donde esta cada cosa

| Tema | `PasosParaProduccion.md` |
| --- | --- |
| Tests, lint, mypy, migraciones antes del tag (las evals las ejecuta CI con gate de regresion) | Fase 1.1 |
| RLS real con `saas_app` | Fase 1.2 |
| Variables Infisical `prod` (HTTPS/HSTS, webhooks firmados, Langfuse sin contenido, allowlist `azp`, cifrado, LLM, R2, limites) | Fase 5 |
| Deploy: backup, `alembic upgrade head`, API + worker ARQ | Fase 8.2 |
| Login, `azp` y retry del webhook de Clerk = no-op (`webhook.dedupe_replay`) | Fase 8.4 |
| Backup en R2 y restore probado fuera de prod | Fase 9 |
| QA manual: auth/tenant, documentos, knowledge, chat con citas, planes | Fase 11 |
| Seguridad operativa | Fase 12 |
| Gaps aceptados y firma | Fase 13 |
| Rollback y kill-switch de coste | Fase 14.3 y 14.4 |

Rollback: `p64` hace `DROP TABLE plans` en el downgrade. En prod nunca `alembic downgrade`; se restaura el backup que hace `deploy.sh` antes de migrar.

## Backup local (solo dev)

Para ensayar migraciones en tu PC (contenedor `saas-postgres`, base `saas`). En prod se usa `deploy/scripts/backup.sh` (Fase 9).

```powershell
docker exec saas-postgres pg_dump -U saas -Fc -d saas -f /tmp/saas.dump
docker cp saas-postgres:/tmp/saas.dump .\saas.dump
```

Restore local (destructivo para esa base; solo en una copia):

```powershell
docker cp .\saas.dump saas-postgres:/tmp/saas.dump
docker exec saas-postgres pg_restore -U saas -d saas --clean --if-exists /tmp/saas.dump
```

## Historial de decisiones

### 2026-09-22 - No-Go

No hay despliegue a produccion. Infisical `prod` y `staging` tienen 0 secretos, el slug `production` no existe, y la QA manual de `Paso07` no esta hecha con una sesion real.

| Dato | Valor |
|------|--------|
| Commit en `origin/RamaCursor01` | `7bc1aea` |
| Alembic local | `p64_plans_entitlements_01` (head) |
| Entorno Infisical usado | `dev` |

Smoke solo en local (`APP_ENV=development`): `/health`, `/health/db`, `/health/redis` 200; `/docs` 200 en development (en `production` el codigo lo desactiva, `app/main.py`).

### 2026-09-30 - Sigue No-Go

Codigo del producto minimo cerrado; bloqueo solo de ops. Se retira staging del camino critico (aplazado) y se elimina Stripe del alcance (D016). Para pasar a Go: completar `PasosParaProduccion.md` Fases 1–13.

## Registro de releases

Una entrada por release, rellenada al terminar la Fase 13 de `PasosParaProduccion.md`:

```text
Release: YYYY-MM-DD HH:MM Europe/Madrid
Decision: Go | No-Go
Tag / commit: vX.Y.Z / <sha>
Alembic: <salida de `alembic current` en prod> (head)
Infisical: prod
Gaps aceptados: <lista de la Fase 13 o "ninguno">
Smoke: OK | parcial
Responsable: <nombre>
```

(sin releases todavia)

## Criterios de aceptacion

- [ ] `PasosParaProduccion.md` Fases 1–12 completadas o con excepcion aceptada por escrito.
- [ ] Entrada firmada en "Registro de releases".
- [x] Langfuse RGPD en codigo: `LANGFUSE_CAPTURE_CONTENT` rechazado fuera de development.
- [x] Coste LLM controlado por plan/cuota en codigo (Pasos 02–04).
