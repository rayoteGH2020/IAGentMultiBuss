-- Extensiones necesarias para la BD principal
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Esto se ejecuta solo la primera vez que se crea el volumen.
-- Si necesitas activar extensiones después, hazlo con una migración Alembic.
