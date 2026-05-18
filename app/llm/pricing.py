"""Estimación de coste EUR por modelo (por 1M tokens). Actualizar cuando cambien tarifas."""

from decimal import Decimal

PRICING: dict[str, dict[str, Decimal]] = {
    "claude-haiku-4-5-20251001": {"input": Decimal("0.90"), "output": Decimal("4.50")},
    "claude-sonnet-4-6": {"input": Decimal("2.80"), "output": Decimal("14.00")},
    "gemini-2.5-flash": {"input": Decimal("0.28"), "output": Decimal("2.30")},
    "gemini-2.5-pro": {"input": Decimal("1.10"), "output": Decimal("4.40")},
    "voyage-3-lite": {"input": Decimal("0.018"), "output": Decimal("0")},
}


def compute_cost_eur(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    rates = PRICING.get(model)
    if rates is None:
        return Decimal("0")
    cost = Decimal(input_tokens) * rates["input"] + Decimal(output_tokens) * rates["output"]
    return (cost / Decimal("1000000")).quantize(Decimal("0.000001"))
