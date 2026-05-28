-- Extensiones de Postgres para la BD principal.
--
-- Este script lo ejecuta el contenedor Postgres SOLO cuando el directorio de
-- datos está vacío (primer arranque o tras borrar docker/data/postgres/).
-- Cambios posteriores aquí NO tienen efecto hasta resetear el volumen.
-- Si necesitas añadir una extensión en una BD existente (dev o producción),
-- hazlo con una migración Alembic (op.execute("CREATE EXTENSION IF NOT EXISTS ...")).
--
-- IF NOT EXISTS en cada CREATE EXTENSION: hace el script idempotente. Si se
-- ejecuta accidentalmente una segunda vez (p. ej. montando el fichero en otro
-- entorno que ya tiene las extensiones), no lanza error.

-- uuid-ossp: proporciona uuid_generate_v4() para generar UUIDs desde SQL.
-- Aunque SQLAlchemy genera los UUIDs en Python (default=uuid4 en los modelos),
-- la extensión es necesaria para:
--   - Queries administrativas manuales que necesiten generar UUIDs.
--   - Inserciones directas en psql durante el desarrollo o debugging.
--   - Compatibilidad con algunas versiones de Alembic que la referencian.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- pgcrypto: funciones de cifrado simérico (pgp_sym_encrypt / pgp_sym_decrypt).
-- Necesaria para cifrar campos sensibles en BD (arquitectura.md §9):
--   - data_sources.connection_encrypted — credenciales de BD del cliente (módulo 3).
--   - Tokens OAuth de integraciones (WhatsApp Business, etc.).
-- Sin esta extensión, los modelos que usen pgcrypto fallarían en runtime.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- vector (pgvector): añade el tipo de dato VECTOR y los operadores de
-- similitud coseno/L2/inner-product. Imprescindible para:
--   - La columna chunks.embedding vector(512) del módulo 2 RAG (voyage-3-lite).
--   - El índice HNSW sobre esa columna (búsqueda de vecinos aproximados).
-- Sin esta extensión, la migración que crea la tabla chunks fallaría con
-- "type 'vector' does not exist".
CREATE EXTENSION IF NOT EXISTS "vector";
