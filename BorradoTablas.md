# Borrado de datos de desarrollo

Script para limpiar completamente las tablas de chat y knowledge en el entorno de desarrollo.

> **ADVERTENCIA**: destructivo e irreversible. Ejecutar solo en desarrollo local. Nunca en producción.

## Jerarquía de tablas y cascadas

```
chat_threads
  └── chat_messages        (FK → chat_threads.id  ON DELETE CASCADE)

knowledge_documents
  └── knowledge_chunks     (FK → knowledge_documents.id  ON DELETE CASCADE)
```

Borrar el padre elimina automáticamente los hijos por CASCADE. No hay otras tablas con FK a estas cuatro.

---

## Script SQL

```sql
-- ============================================================
-- BORRADO COMPLETO — solo entornos de desarrollo
-- Ejecutar como superuser o con RLS desactivado para el rol.
-- ============================================================

-- Desactiva temporalmente RLS para que el DELETE no quede
-- bloqueado por el filtro de tenant (solo necesario si el rol
-- de conexión tiene RLS activo sobre estas tablas).
SET session_replication_role = 'replica';

-- Hijos primero (redundante con CASCADE, pero explícito y seguro)
DELETE FROM knowledge_chunks;
DELETE FROM chat_messages;

-- Padres (el CASCADE eliminaría los hijos si no se borraron antes)
DELETE FROM knowledge_documents;
DELETE FROM chat_threads;

-- Restaura el comportamiento normal de triggers y RLS
SET session_replication_role = 'origin';
```

---

## Cómo ejecutarlo

### Opción A — psql directo

```bash
infisical run -- psql "$POSTGRES_URL" -c "
SET session_replication_role = 'replica';
DELETE FROM knowledge_chunks;
DELETE FROM chat_messages;
DELETE FROM knowledge_documents;
DELETE FROM chat_threads;
SET session_replication_role = 'origin';
"
```

### Opción B — fichero SQL

```bash
infisical run -- psql "$POSTGRES_URL" -f BorradoTablas.sql
```

Crea `BorradoTablas.sql` copiando el bloque SQL de arriba.

### Opción C — Python (dentro del entorno virtual)

```bash
infisical run -- uv run python -c "
import asyncio
from sqlalchemy import text
from app.core.db import engine

async def main():
    async with engine.begin() as conn:
        await conn.execute(text(\"SET session_replication_role = 'replica'\"))
        for tabla in ['knowledge_chunks', 'chat_messages', 'knowledge_documents', 'chat_threads']:
            result = await conn.execute(text(f'DELETE FROM {tabla}'))
            print(f'{tabla}: {result.rowcount} filas eliminadas')
        await conn.execute(text(\"SET session_replication_role = 'origin'\"))

asyncio.run(main())
"
```

---

## Verificación posterior

```sql
SELECT 'chat_threads'       AS tabla, COUNT(*) FROM chat_threads
UNION ALL
SELECT 'chat_messages',               COUNT(*) FROM chat_messages
UNION ALL
SELECT 'knowledge_documents',         COUNT(*) FROM knowledge_documents
UNION ALL
SELECT 'knowledge_chunks',            COUNT(*) FROM knowledge_chunks;
```

Todas las filas deben ser 0.
