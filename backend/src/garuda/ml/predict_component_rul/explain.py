"""Inspection recommendations within the existing MaintenanceAdvice contract."""

from garuda.ml.predict_component_rul.schemas import ComponentRisk, MaintenanceAdvice


def explain(
    item: ComponentRisk, horizon_hours: float, inspection_hours_remaining: float
) -> MaintenanceAdvice | None:
    if item.p_fail_next >= 0.1 or inspection_hours_remaining <= horizon_hours:
        return MaintenanceAdvice(
            tail=item.tail, component=item.component, action="inspect"
        )
    return None
