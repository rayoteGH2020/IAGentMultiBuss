# Re-exporta los schemas públicos del paquete para que los importadores puedan
# escribir `from app.schemas import Factura` en lugar de
# `from app.schemas.invoice import Factura`. Mantiene la API pública estable
# aunque los schemas se reorganicen internamente en submódulos.
from app.schemas.invoice import Factura, LineaFactura

# __all__ documenta la API pública del paquete y evita que `from app.schemas
# import *` exporte símbolos privados o importados transitivamente.
__all__ = ["Factura", "LineaFactura"]
