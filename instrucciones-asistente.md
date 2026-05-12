# Instrucciones para el asistente (Cursor / Claude Code)

> **Documentación canónica del proyecto:** diseño y dominio en **`arquitectura.md`**; reglas de código y stack en **`Agents.md`** (raíz; a veces enlazado como `AGENTS.md` / `CLAUDE.md`); tareas de implementación en **`PasoXX.md`**. Este fichero es solo **guía de uso** del asistente para personas.

> Esta guía explica cómo configurar y usar Cursor o Claude Code de forma efectiva en este proyecto. 
Si vienes de C#/MVC y es tu primera vez con un asistente de IA pareja-de-código, lee la sección 1 con calma.

---

## 1. Modelo mental: cómo trabajar con un asistente de código

Un asistente como Cursor o Claude Code **no es Visual Studio**. Es más parecido a tener un programador junior muy rápido pero olvidadizo a tu lado. Lo que esto implica:

- **No tiene memoria entre sesiones**. Cada conversación parte de cero. Por eso existen `Agents.md`, `arquitectura.md` y los `PasoXX.md`: para que pueda recargar el contexto.
- **Hace lo que le pides, no lo que necesitas**. Si pides "monta el proyecto entero", lo intentará y producirá algo a medias. Pídele tareas concretas y delimitadas.
- **Inventa si no sabe**. Si dudas si está inventando un nombre de función o atributo, pídele que verifique con la documentación.
- **Es excelente para volumen, no para criterio**. Las decisiones arquitectónicas son tuyas. Su trabajo es ejecutar.

La habilidad principal que vas a desarrollar no es escribir código, es **escribir instrucciones para que el asistente escriba el código correcto**.

---

## 2. Configuración inicial

### Cursor

1. Instalar Cursor (cursor.com).
2. Abrir la carpeta del proyecto.
3. **`Agents.md`** en la raíz es la **fuente canónica** de reglas de código. Al iniciar un chat, pide explícitamente leerlo (y `arquitectura.md` / `PasoXX.md` cuando aplique).
4. Este repo incluye **`.cursor/rules/project.mdc`** con `alwaysApply: true`: resumen crítico para Cursor sin volcar todo `Agents.md` en cada conversación. Si cambian reglas fuertes del stack, actualiza **primero** `Agents.md` y **luego** alinea el resumen en `project.mdc` (ver sección 4).
5. Configurar el modelo: Settings → Models → Activar Claude Sonnet 4.6+ o el más reciente. Para tareas largas usar el modelo más capaz disponible.
6. Activar "Auto-include open files" en el contexto.

### Claude Code

1. Instalar: `npm install -g @anthropic-ai/claude-code`.
2. En la raíz del proyecto, ejecutar `claude` desde terminal.
3. Renombrar `AGENTS.md` a `CLAUDE.md` o crear un symlink: `ln -s AGENTS.md CLAUDE.md`. Claude Code lo lee automáticamente.
4. Configurar permisos al primer uso (qué comandos puede ejecutar sin pedir).

### Para usar ambos (recomendado al principio)

Mantén **un solo** `AGENTS.md` en la raíz. Para **Claude Code**, enlace al mismo contenido:
```bash
ln -s AGENTS.md CLAUDE.md
```
En **Cursor** no enlaces `AGENTS.md` dentro de `.cursor/rules/` como copia completa: el equilibrio recomendado es el **`.cursor/rules/project.mdc`** ya versionado (resumen + `alwaysApply`). Así evitas duplicar cientos de líneas y que dos ficheros diverjan.

---

## 3. Flujo de trabajo recomendado

### Al empezar cada sesión

1. **Abre la conversación con contexto explícito**:
   ```
   Lee AGENTS.md y arquitectura.md. Vamos a trabajar en PasoXX.md.
   Confírmame que has leído los tres antes de empezar.
   ```
2. **Espera a que confirme** que ha cargado los ficheros. Si responde sin haberlos leído, insiste.
3. **Pide el paso concreto**:
   ```
   Vamos con Paso03.md. Implementa todos los checks de la sección "Tareas".
   No introduzcas dependencias no listadas en arquitectura.md.
   ```

### Durante el trabajo

1. **Una sesión = un Paso**, idealmente. Si terminas el Paso y queda tiempo, empieza el siguiente en una conversación nueva.
2. **Revisa antes de aceptar**. Aunque Cursor permita aplicar cambios masivos, hazlo en pequeños bloques y revisa cada archivo.
3. **Si el asistente toma una decisión arquitectónica nueva** (eligen una librería, definen un patrón), detenle y discútelo antes de aceptar.
4. **Si propone añadir una dependencia nueva**, pregúntale por qué y si está en `arquitectura.md`. Si no, decisión consciente.

### Al cerrar el Paso

1. **Verifica los criterios de aceptación** del fichero PasoXX.md.
2. **Ejecuta los tests** que toquen.
3. **Haz commit** con mensaje según convención (`feat: ...`).
4. **Actualiza el fichero PasoXX.md** marcando los checks como completados.
5. **Si surgió una decisión nueva**, añádela al final de `arquitectura.md` (sección 13 — Decision Log).

---

## 4. Reglas de Cursor (`.cursor/rules/project.mdc`)

El fichero **`.cursor/rules/project.mdc`** del repositorio declara `alwaysApply: true` y contiene un **resumen** alineado con `AGENTS.md` (stack, capas, convenciones mínimas, multi-tenant, prohibidos). Cursor lo inyecta en el contexto de forma fija sin ocupar toda la ventana con la normativa completa.

- **Normativa completa:** sigue siendo `AGENTS.md` (más `arquitectura.md` cuando toque).
- **Mantenimiento:** si cambian reglas de proyecto, actualiza `AGENTS.md` y luego el bloque resumido en `project.mdc` para que no queden en conflicto.

Si en algún equipo necesitáis **coerción máxima** y aceptáis el coste de contexto, podéis sustituir el resumen por un **único** enlace simbólico a `AGENTS.md` bajo `.cursor/rules/`, pero **no** mantengáis dos copias literales del mismo texto (riesgo de divergencia).

---

## 5. Prompts útiles para empezar conversaciones

### Inicio de sesión con un Paso nuevo
```
Lee AGENTS.md, arquitectura.md y Paso07.md.
Confírmame que los has leído resumiendo en una línea el objetivo del paso.
Luego dime qué ficheros vas a crear y cuáles modificar, en una lista, antes de empezar.
```

### Revisión de código generado
```
Antes de pasar al siguiente subtask, revisa lo que has generado en este paso contra:
1. Las reglas de AGENTS.md
2. Los criterios de aceptación de PasoXX.md
3. El patrón de capas de arquitectura.md sección 4
Lista cualquier desviación.
```

### Cuando algo no funciona
```
Estoy obteniendo este error: [pegar].
Ficheros relevantes: [pegar rutas].
NO modifiques nada todavía. Primero diagnostica la causa raíz.
Cuando tengas una hipótesis, propón el cambio mínimo necesario.
```

### Pedir tests
```
Genera tests para [función/módulo]. Sigue las convenciones de la sección "Tests" en AGENTS.md.
Cubre casos: happy path, error de validación, fallo de proveedor externo, edge case con datos vacíos.
```

### Refactor
```
Refactoriza [función/archivo] para cumplir con [regla específica].
NO cambies funcionalidad. NO añadas features.
Mantén la firma pública intacta.
```

---

## 6. Anti-patrones de uso del asistente

### ❌ "Móntame el proyecto"
El asistente generará 20 archivos a medias, con dependencias inventadas y patrones que se contradicen. Pídele un Paso a la vez.

### ❌ Aceptar sin leer
Cursor te permite aplicar cambios a 10 archivos con un click. Resiste. Revisa cada archivo antes de aceptar. Lo que parece código correcto a primera vista a veces tiene bugs sutiles.

### ❌ Diálogo eterno en la misma conversación
Cuando una conversación pasa de ~50 mensajes, el contexto se degrada. Termina el Paso, cierra la conversación, abre una nueva.

### ❌ Ignorar las preguntas del asistente
Si te pregunta una decisión (qué nombre poner a un endpoint, qué validación aplicar), no respondas "lo que tú creas". Responde con criterio o pídele opciones con pros y contras.

### ❌ Dejarle "mejorar" el código sin pedírselo
A veces ofrece refactorizar lo que ya está bien. Acéptalo solo si tiene una razón concreta y demostrable.

### ❌ Confiar en lo que sabe sobre librerías nuevas
Si trabaja con una librería poco conocida o muy nueva, pídele que lea los docs o que te muestre cómo verifica que la API es esa.

---

## 7. Cuándo NO usar el asistente

- **Decisiones de producto**: qué módulo construir primero, qué precio poner, qué cliente buscar primero. Eso es tuyo.
- **Decisiones arquitectónicas mayores**: si cambias de stack o introduces una capa nueva, decide tú con calma y luego pídele que ejecute.
- **Seguridad crítica**: revisa tú línea por línea el código de auth, validación de tenant, RLS, gestión de secretos.
- **Migrations Alembic**: déjalo generar el `--autogenerate`, pero revisa la migración a mano antes de commit. Es un punto donde un error es muy caro.

---

## 8. Cuándo SÍ apoyarte fuerte en el asistente

- **Boilerplate**: configuración inicial, Dockerfiles, docker-compose, GitHub Actions.
- **Templates Jinja2**: HTML repetitivo con Tailwind.
- **Migrations**: generar el SQL inicial de un modelo nuevo.
- **Tests**: pytest fixtures, casos de prueba, mocks.
- **Prompts LLM**: iteración rápida de versiones.
- **Refactor mecánico**: renombrar, mover, extraer.
- **Documentación**: README de módulos, docstrings.

---

## 9. Flujo Git recomendado

1. Una rama por Paso: `git checkout -b paso/03-db-models`.
2. Commits frecuentes durante el desarrollo del Paso (`wip:` ok).
3. Al terminar el Paso: `git rebase -i` para limpiar historial.
4. Mensaje final: `feat(paso-03): models iniciales + RLS`.
5. PR a `main` con descripción que enlace al PasoXX.md.
6. CI verde antes de merge.

---

## 10. Errores comunes los primeros días

### "El asistente ignora AGENTS.md"
- Verifica que el fichero está en la raíz.
- En Cursor: revisa que exista `.cursor/rules/project.mdc` y que las reglas del proyecto estén activas (Settings → Rules).
- Al inicio de cada conversación, pídele explícitamente que lea `AGENTS.md` y confirme; el `.mdc` es un resumen, no sustituye el documento completo.

### "Me genera código con LangChain aunque le he dicho que no"
- Insiste con `AGENTS.md` y la regla "NO LangChain como columna vertebral".
- Si insiste, recházalo y pídele alternativas sin esa dependencia.

### "Cambia más cosas de las que le pido"
- Usa Cursor en modo "Edit" (cambios localizados) más que "Composer" (multi-archivo).
- Sé explícito: "modifica SOLO el archivo X. No toques nada más".

### "Inventa funciones de FastAPI/SQLAlchemy"
- Pídele que verifique con `pip show <librería>` o que abra los docs oficiales.
- Si la librería es muy nueva, búscalo tú y pégale la API correcta.

### "Las migraciones de Alembic salen mal"
- Genera con `--autogenerate` pero **siempre** revisa el SQL antes de aplicar.
- Si Alembic detecta diferencias raras, hay drift entre el modelo y la BD; resuelve antes.

---

## 11. Checklist antes de cerrar cada Paso

- [ ] He revisado todos los archivos modificados a mano.
- [ ] Los tests del paso pasan localmente.
- [ ] `ruff check . && ruff format . && mypy app` pasan.
- [ ] La migración (si existe) está revisada y se aplica limpiamente.
- [ ] He probado el flujo end-to-end en el navegador (si aplica).
- [ ] He marcado los checks del PasoXX.md como completados.
- [ ] He hecho commit con mensaje convencional.
- [ ] He cerrado la conversación con el asistente para no contaminar la siguiente.

---

## 12. Glosario rápido

- **AGENTS.md / CLAUDE.md**: fichero raíz con reglas para el asistente; es la normativa completa. Conviene pedir su lectura al inicio de cada sesión.
- **`.cursor/rules/project.mdc`**: reglas críticas resumidas que Cursor aplica siempre (`alwaysApply`). Complementa, no reemplaza, `AGENTS.md`.
- **Paso**: unidad de trabajo equivalente a una sesión con el asistente (~2-6h).
- **Patrón página/fragmento**: cada endpoint web responde con layout completo o solo fragmento según `HX-Request`.
- **Multi-tenancy**: aislamiento de datos por cliente (`tenant_id` + RLS).
- **RLS (Row-Level Security)**: política de Postgres que filtra filas según contexto de sesión.
- **Structured output**: cuando un LLM devuelve un objeto Pydantic validado en lugar de texto libre.
- **Eval**: test de calidad del comportamiento de un LLM contra un dataset con ground truth.
