"""Schema Pydantic de factura emitida/extraída (módulo 1).

Este módulo define el contrato entre tres capas:
  - El LLM (via Instructor): rellena estos schemas con structured output.
  - invoice_service.apply_extraction_result: lee los campos y los persiste en BD.
  - El eval runner (app/evals/runners/extraction.py): compara campos contra ground truth.

Los atributos `description` de cada Field NO son documentación interna: Instructor
los incluye en el schema JSON que envía al LLM como parte del prompt, por lo que
su redacción afecta directamente a la calidad de la extracción.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LineaFactura(BaseModel):
    # strict=False es defensa secundaria; el fix real son los validators de abajo.
    # instructor.from_genai aplica strict=True al deserializar el function_call
    # de Gemini, ignorando ConfigDict(strict=False). Por eso necesitamos un
    # @field_validator(mode="before") que convierta int/float → Decimal antes
    # de que Pydantic valide el tipo (ver _coerce_decimal).
    model_config = ConfigDict(strict=False)

    descripcion: str = Field(description="Descripción del concepto o producto")
    # ge=0 (no gt=0): exclusiveMinimum no es aceptado por el schema validator de
    # google-genai (rechaza la propiedad como "extra_forbidden"). Para conservar
    # la semántica "cantidad > 0" se puede añadir un model_validator post-parse.
    cantidad: Decimal = Field(ge=0, description="Cantidad o unidades")
    # precio_unitario puede ser 0 en líneas de regalo, descuento total o
    # conceptos gratuitos incluidos en la factura.
    precio_unitario: Decimal = Field(ge=0, description="Precio por unidad sin IVA")
    total: Decimal = Field(ge=0, description="Total de la línea (cantidad x precio)")

    @field_validator("cantidad", "precio_unitario", "total", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: object) -> object:
        # mode="before": corre antes de la validación de tipo, así que recibe el
        # valor crudo que entrega Instructor desde el function_call de Gemini
        # (int/float). Convertimos con str() para preservar la representación
        # decimal exacta y evitar artefactos binarios de float (0.1 + 0.2 = 0.30000...).
        if isinstance(v, int | float):
            return Decimal(str(v))
        return v


class Factura(BaseModel):
    # Ver nota en LineaFactura sobre strict=False + validators.
    # Gemini devuelve fecha como string ISO y los importes como int/float; los
    # field_validator de abajo hacen la conversión antes de que Pydantic valide.
    model_config = ConfigDict(strict=False)

    # `date` (no `datetime`): las facturas tienen fecha de emisión, no timestamp.
    # Usar date también relaja el parsing: el LLM no necesita incluir hora ni zona
    # horaria, lo que reduce errores de formato en respuestas del modelo.
    fecha: date = Field(description="Fecha de emisión de la factura")
    proveedor: str = Field(description="Razón social o nombre del emisor")
    cif_nif: str = Field(
        description="CIF, NIF o NIE del emisor en España",
        # Patrón para identificadores fiscales españoles:
        # - NIF persona física: 8 dígitos + letra (9 chars).
        # - CIF sociedad: letra + 7 dígitos + dígito/letra (9 chars).
        # - NIE extranjero: X/Y/Z + 7 dígitos + letra (9 chars).
        # El rango 8-10 cubre variantes con guiones u otros separadores que el
        # LLM podría omitir al normalizar, y algún formato especial de 10 chars.
        # MAYÚSCULAS: el LLM normaliza a mayúsculas según la descripción del campo.
        pattern=r"^[A-Z0-9]{8,10}$",
    )
    # Opcional: no todas las facturas (tickets, recibos simplificados) muestran
    # número de factura. None indica que el LLM no lo encontró en el documento.
    numero_factura: str | None = Field(
        default=None,
        description="Número o serie de factura si aparece",
    )

    base_imponible: Decimal = Field(ge=0, description="Suma sin IVA")
    iva_percent: Decimal = Field(
        ge=0,
        le=100,
        # La descripción enumera los tipos de IVA vigentes en España (0%, 4%
        # superreducido, 10% reducido, 21% general) para que el LLM no invente
        # valores intermedios. El validator no los restringe porque pueden
        # existir facturas de otros países con tipos distintos.
        description="Porcentaje IVA aplicado (0, 4, 10, 21)",
    )
    iva_amount: Decimal = Field(ge=0, description="Importe del IVA en euros")
    total: Decimal = Field(ge=0, description="Total final con IVA")
    # Default "EUR": la mayoría de las pymes españolas solo trabajan en euros.
    # El LLM puede extraer otro código ISO 4217 si la factura lo indica.
    currency: str = Field(default="EUR", description="ISO 4217, normalmente EUR")

    # default_factory=list (no default=[]): en Pydantic v2, un default mutable
    # compartido entre instancias provoca bugs sutiles; factory crea una nueva
    # lista por instancia.
    lineas: list[LineaFactura] = Field(
        default_factory=list,
        description="Líneas de detalle si aparecen",
    )

    # `float` (no Decimal): confidence es una probabilidad (0-1), no un importe
    # monetario. La imprecisión de float a este rango es irrelevante para el uso.
    # La descripción está en segunda persona ("Tu confianza…") porque Instructor
    # la incluye literalmente en el schema que el LLM ve; dirigirse al modelo
    # en segunda persona mejora la adherencia a la instrucción.
    confidence: float = Field(
        ge=0,
        le=1,
        description="Tu confianza global en la extracción (0=incierto, 1=seguro)",
    )

    @field_validator("fecha", mode="before")
    @classmethod
    def _coerce_fecha(cls, v: object) -> object:
        # Gemini devuelve la fecha como string ISO-8601 (cumple el `description`
        # del campo). Con instructor.from_genai forzando strict, Pydantic
        # rechazaría el str y exigiría datetime.date nativo. fromisoformat soporta
        # "YYYY-MM-DD" (date) y "YYYY-MM-DDTHH:MM:SS" (datetime) sin dependencias.
        if isinstance(v, str):
            return date.fromisoformat(v)
        return v

    @field_validator(
        "base_imponible",
        "iva_percent",
        "iva_amount",
        "total",
        mode="before",
    )
    @classmethod
    def _coerce_decimal(cls, v: object) -> object:
        # str(v) preserva la representación decimal exacta (Decimal(0.1) sería
        # 0.1000000000000000055..., pero Decimal(str(0.1)) es 0.1 limpio).
        if isinstance(v, int | float):
            return Decimal(str(v))
        return v

    @model_validator(mode="after")
    def _check_totals_coherent(self) -> Factura:
        # mode="after": el validator corre después de que todos los campos han
        # sido validados y convertidos a Decimal, por lo que self.base_imponible
        # etc. son ya valores seguros con los que operar.
        #
        # En lugar de lanzar ValidationError (que rechazaría la extracción
        # completa), se penaliza la confianza cuando la aritmética no cuadra.
        # Esto permite que el revisor humano vea el resultado con advertencia en
        # lugar de recibir un error opaco. La tolerancia de 0.01 EUR es la misma
        # que usa el eval runner para comparar importes.
        suma = (self.base_imponible + self.iva_amount).quantize(Decimal("0.01"))
        total_q = self.total.quantize(Decimal("0.01"))
        if abs(suma - total_q) > Decimal("0.01"):
            # object.__setattr__ es necesario porque los modelos Pydantic v2
            # sobreescriben __setattr__ para aplicar validación; desde dentro
            # de un validator, la asignación directa (self.confidence = …)
            # puede entrar en recursión o ser rechazada. La llamada a object
            # bypasses la capa de Pydantic de forma segura en este contexto.
            object.__setattr__(self, "confidence", min(self.confidence, 0.5))
        return self
