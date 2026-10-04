"""Shape LP results into the unchanged public output, logging rounding loss."""

import logging
from math import floor

from garuda.optimization.compute_sortie_capacity.model import FAMILIES
from garuda.optimization.compute_sortie_capacity.schemas import (
    ComputeSortieCapacityOutput,
)

logger = logging.getLogger(__name__)


def explain(problem, solution) -> ComputeSortieCapacityOutput:
    capacity, totals, bottlenecks, prices = {}, {}, {}, {}
    for base in problem.bases:
        result = solution.bases[base.base_id]
        counts = [
            floor(round(v) if abs(v - round(v)) <= 1e-7 else v) for v in result.hourly
        ]
        capacity[base.base_id] = counts
        totals[base.base_id] = sum(counts)
        prices[base.base_id] = result.shadow_prices.copy()
        bottlenecks[base.base_id] = (
            "weather"
            if not any(base.open_hours)
            else max(FAMILIES, key=lambda family: result.shadow_prices[family])
        )
        loss = sum(result.hourly) - sum(counts)
        if loss > 1e-7:
            logger.info(
                "Sortie capacity at %s: LP total %.6f, rounded total %d, rounding loss %.6f",
                base.base_id,
                sum(result.hourly),
                sum(counts),
                loss,
            )
    return ComputeSortieCapacityOutput(
        capacity=capacity, totals=totals, bottlenecks=bottlenecks, shadow_prices=prices
    )
