"""Estimación de coste EUR por modelo (por 1M tokens). Actualizar cuando cambien tarifas."""

from decimal import Decimal

# Tarifas en EUR por millón de tokens (la unidad que usan todos los proveedores).
# Se usan Decimal (no float) para evitar errores de representación binaria al
# acumular costes de muchas llamadas en el billing mensual por tenant.
# Al añadir un nuevo modelo, añadir su entrada aquí; compute_cost_eur devuelve
# 0 para modelos no conocidos (fail soft, preferible a crashear en producción).
PRICING: dict[str, dict[str, Decimal]] = {
    "claude-haiku-4-5-20251001": {"input": Decimal("0.90"), "output": Decimal("4.50")},
    "claude-sonnet-4-6": {"input": Decimal("2.80"), "output": Decimal("14.00")},
    "gemini-2.5-flash": {"input": Decimal("0.28"), "output": Decimal("2.30")},
    "gemini-2.5-pro": {"input": Decimal("1.10"), "output": Decimal("4.40")},
    # voyage-3-lite: modelo de embeddings; no tiene tokens de output.
    "voyage-3-lite": {"input": Decimal("0.018"), "output": Decimal("0")},
}


def compute_cost_eur(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    rates = PRICING.get(model)
    if rates is None:
        # Modelo desconocido: devolver 0 en lugar de fallar. El coste puede
        # quedar subcontabilizado, pero la llamada LLM no se interrumpe por
        # un error de pricing. Se detectará al revisar el dashboard de costes.
        return Decimal("0")
    # Fórmula: (tokens * tarifa_por_millón) / 1_000_000
    # Se divide al final (no se divide la tarifa primero) para preservar
    # la precisión decimal durante la multiplicación.
    cost = Decimal(input_tokens) * rates["input"] + Decimal(output_tokens) * rates["output"]
    # quantize("0.000001"): 6 decimales, que coincide con Numeric(10,6) en la
    # columna llm_calls.cost_eur. Evita pérdida de precisión en el INSERT.
    return (cost / Decimal("1000000")).quantize(Decimal("0.000001"))
