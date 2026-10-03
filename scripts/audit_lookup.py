"""Rastrea en ``audit_log`` qué se hizo con una persona o un fichero (SADM, P2c-7).

La metadata de auditoría guarda seudónimos (HMAC con ``AUDIT_PSEUDONYM_KEY``), no
emails ni nombres de fichero en claro. Este script recalcula el seudónimo con el
dato que conoces y saca la línea de tiempo en todos los tenants (o en uno):

- ``--email``: lo que se hizo con esa persona (alta, baja, solicitudes,
  calendario) y lo que hizo ella (por su ``user_id``), también si su cuenta se
  borró y se anonimizó (D021).
- ``--name``: cuentas activas cuyo nombre contiene el texto y profesionales con
  ese nombre exacto (sin distinguir mayúsculas ni tildes). Tras borrar una cuenta,
  el nombre ya no está: usa el email.
- ``--filename`` (nombre exacto, sin distinguir mayúsculas) o ``--sha256`` (hash
  del contenido): subidas y borrados de ese fichero.

El propio rastreo queda auditado en cada tenant con resultados
(``sadm.audit_lookup``, sin el dato buscado). Necesita la misma
``AUDIT_PSEUDONYM_KEY`` con la que se escribieron las entradas: ejecútalo con el
entorno de Infisical correspondiente.

Uso:
  infisical run --env=prod -- uv run python scripts/audit_lookup.py --email ana@empresa.com
  infisical run -- uv run python scripts/audit_lookup.py --filename "Nómina marzo.pdf" \\
    --tenant-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from app.core.db import session_scope
from app.services import audit_lookup_service


async def _run(criteria: audit_lookup_service.LookupCriteria) -> int:
    async with session_scope() as db:
        result = await audit_lookup_service.lookup(db, criteria)
        await db.commit()
    users = ", ".join(sorted(str(user_id) for user_id in result.user_ids)) or "-"
    sys.stdout.write(f"Usuarios relacionados: {users}\n")
    sys.stdout.write(f"Entradas: {len(result.entries)}\n")
    for entry in result.entries:
        sys.stdout.write(audit_lookup_service.entry_summary(entry) + "\n")
    return len(result.entries)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--email", help="Email de la persona")
    parser.add_argument("--name", help="Nombre de la persona o del profesional")
    parser.add_argument("--filename", help="Nombre exacto del fichero")
    parser.add_argument("--sha256", help="SHA-256 del contenido del fichero")
    parser.add_argument("--tenant-id", type=UUID, help="Limitar a un tenant")
    args = parser.parse_args()
    criteria = audit_lookup_service.LookupCriteria(
        email=args.email,
        name=args.name,
        filename=args.filename,
        sha256=args.sha256,
        tenant_id=args.tenant_id,
    )
    if not criteria.labels():
        parser.error("indica al menos --email, --name, --filename o --sha256")
    asyncio.run(_run(criteria))


if __name__ == "__main__":
    main()
