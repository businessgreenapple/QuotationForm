from __future__ import annotations

from typing import Tuple, Iterable


def calculate_line_totals(quantity: int, unit_price: float, unit_cost: float) -> Tuple[float, float]:
    q = max(0, int(quantity))
    lp = max(0.0, float(unit_price))
    lc = max(0.0, float(unit_cost))
    return q * lp, q * lc


def calculate_estimate_totals(items: Iterable) -> tuple[float, float, float, float]:
    subtotal_price = float(sum(getattr(i, 'line_total_price', 0.0) for i in items))
    subtotal_cost = float(sum(getattr(i, 'line_total_cost', 0.0) for i in items))
    gross_profit = subtotal_price - subtotal_cost
    gross_margin_rate = 0.0
    if subtotal_price > 0:
        gross_margin_rate = gross_profit / subtotal_price
    return subtotal_price, subtotal_cost, gross_profit, gross_margin_rate
