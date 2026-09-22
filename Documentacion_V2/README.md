# Documentacion_V2

Fecha: 2026-08-04
Estado: fuente de planificacion V2 para continuar el repositorio actual, no para reescribirlo desde cero.

## Decision principal

No se reinicia la aplicacion. El codigo actual tiene suficiente estructura, seguridad y tests como para evolucionarlo. Lo que se reinicia es la documentacion operativa: los documentos antiguos quedan como historico salvo que esta carpeta los referencie expresamente.

## Como usar esta carpeta

1. Leer `AGENTS.md`.
2. Leer `Arquitectura_V2.md`.
3. Leer `Decision_Log.md`.
4. Elegir el `PasoXX_*.md` activo.
5. Implementar solo lo que diga ese paso.
6. Actualizar el paso con evidencia: ficheros tocados, tests ejecutados y validaciones manuales pendientes.

## Mapa de documentos

| Fichero | Proposito |
| --- | --- |
| `AGENTS.md` | Reglas operativas V2 para asistentes y humanos. |
| `Arquitectura_V2.md` | Arquitectura vigente, estado real y arquitectura objetivo. |
| `Decision_Log.md` | Decisiones que no deben reabrirse sin motivo explicito. |
| `Seguridad_V2.md` | Modelo de seguridad, riesgos residuales y checklist obligatoria. |
| `Planes_Entitlements.md` | Diseno de planes, features, limites y overrides por tenant. |
| `SADM_V2.md` | Consola SuperAdmin: alcance permitido, prohibiciones y controles. |
| `Backlog_Priorizado.md` | Orden recomendado de trabajo. |
| `Paso00_Auditoria_Base.md` | Auditoria de base antes de seguir construyendo. |
| `Paso01_Seguridad_Residual.md` | Cierre de riesgos P0/P1 de seguridad. |
| `Paso02_Planes_Catalogo.md` | Catalogo de planes y resolucion de entitlements. |
| `Paso03_Planes_Gates.md` | Gates por feature en rutas, sidebar, workers y webhooks. |
| `Paso04_Cuotas_Costes.md` | Limites por plan, cuotas, budgets y circuit breakers. |
| `Paso05_SADM_Consolidacion.md` | Consolidacion de la consola SADM. |
| `Paso06_Documentos_Producto.md` | Mejora del modulo documental y validaciones de calidad. |
| `Paso07_Chat_IA_Seguridad.md` | Endurecimiento de chats, tools, RAG y canales externos. |
| `Paso08_Analytics_SQL_ReadOnly.md` | Analitica conversacional con SQL solo lectura. |
| `Paso09_Billing_Stripe.md` | Billing real con Stripe cuando planes ya existan. |
| `Paso10_QA_Release_Produccion.md` | QA manual, release y operacion de produccion. |
| `PasosParaProduccion.md` | Checklist consolidada de go-live (variables, Clerk, infra, QA, rollback). |

## Estado del repositorio observado

Evidencias revisadas el 2026-08-04:

- Existe implementacion real en `app/`, `migrations/`, `tests/`, `.github/workflows/`.
- Hay capa LLM propia, observabilidad, prompts versionados y tests.
- Hay RLS en migraciones con `FORCE ROW LEVEL SECURITY` para tablas tenant.
- Hay middleware de Clerk, CSRF, security headers y guardas SADM.
- Hay consola SADM parcialmente implementada.
- Hay documentacion antigua con decisiones historicas contradictorias.

## Regla de oro

Si una instruccion antigua contradice `Documentacion_V2`, gana `Documentacion_V2`. Si la contradiccion afecta a seguridad, parar y pedir decision explicita.
