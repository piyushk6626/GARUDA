"""Independent continuous LPs per base, with solver duals and feasibility checks."""

from dataclasses import dataclass
from math import isfinite
from time import monotonic

from garuda.contracts.tool import ToolInputError

FAMILIES = ("aircraft", "ground_crew", "fuel", "munitions", "runway", "crew")


@dataclass(frozen=True)
class BaseSolution:
    hourly: tuple[float, ...]
    shadow_prices: dict[str, float]


@dataclass(frozen=True)
class Solution:
    bases: dict[str, BaseSolution]


def solver_factory(seconds, seed):
    import pulp

    highs = pulp.HiGHS(msg=False, timeLimit=seconds, threads=1, random_seed=seed)
    if highs.available():
        return highs
    cbc_class = getattr(pulp, "PULP_CBC_CMD", None) or pulp.COIN_CMD
    cbc = cbc_class(
        msg=False, timeLimit=seconds, threads=1, options=[f"randomSeed {seed}"]
    )
    if cbc.available():
        return cbc
    raise ToolInputError(
        "compute_sortie_capacity requires HiGHS or CBC; install the backend opt extra"
    )


def variable(model, name):
    import pulp

    if hasattr(model, "add_variable"):
        return model.add_variable(name, lowBound=0)
    return pulp.LpVariable(name, lowBound=0)


def constraints(model):
    return (
        model.constraints()
        if callable(model.constraints)
        else model.constraints.values()
    )


def overlap(turnaround, current, previous):
    """Uniform launches within each hour; fractional overlap of the trailing window."""
    return min(1.0, max(0.0, turnaround - (current - previous)))


def build_model(base):
    import pulp

    model = pulp.LpProblem("sortie_capacity", pulp.LpMaximize)
    periods = range(base.horizon_hours)
    x = {
        (g, p, c): variable(model, f"sortie_{g}_{p}_{c}")
        for g, group in enumerate(base.groups)
        for p in periods
        for c in range(len(group.loads))
    }
    sorties = {
        (g, p): pulp.lpSum(x[g, p, c] for c in range(len(group.loads)))
        for g, group in enumerate(base.groups)
        for p in periods
    }
    hourly = {
        p: pulp.lpSum(sorties[g, p] for g in range(len(base.groups))) for p in periods
    }
    model += pulp.lpSum(x.values())
    labels = {}

    def add(expression, family):
        name = f"limit_{len(labels)}"
        model.addConstraint(expression, name)
        labels[name] = family

    for g, group in enumerate(base.groups):
        for p in periods:
            add(
                pulp.lpSum(
                    overlap(group.turnaround_hours, p, q) * sorties[g, q]
                    for q in range(p + 1)
                    if overlap(group.turnaround_hours, p, q)
                )
                <= group.available[p],
                "aircraft",
            )
    codes = {code for group in base.groups for load in group.loads for code in load}
    for p in periods:
        add(
            pulp.lpSum(
                group.turnaround_hours * sorties[g, p]
                for g, group in enumerate(base.groups)
            )
            <= base.ground_hours,
            "ground_crew",
        )
        add(2 * hourly[p] <= base.runway_ops, "runway")
        add(
            pulp.lpSum(
                overlap(group.turnaround_hours, p, q) * sorties[g, q]
                for g, group in enumerate(base.groups)
                for q in range(p + 1)
                if overlap(group.turnaround_hours, p, q)
            )
            <= base.shelters,
            "aircraft",
        )
        if not base.open_hours[p]:
            add(hourly[p] <= 0, "weather")
        add(
            pulp.lpSum(
                group.fuel_kg * sorties[g, q]
                for g, group in enumerate(base.groups)
                for q in range(p + 1)
            )
            <= base.fuel_kg,
            "fuel",
        )
        for code in sorted(codes):
            add(
                pulp.lpSum(
                    load.get(code, 0) * x[g, q, c]
                    for g, group in enumerate(base.groups)
                    for c, load in enumerate(group.loads)
                    for q in range(p + 1)
                )
                <= base.stock.get(code, 0),
                "munitions",
            )

    # Individual fractional seat assignments prevent double counting cross-qualified crew.
    z = {
        (i, g, p): variable(model, f"seat_{i}_{g}_{p}")
        for i, member in enumerate(base.crew)
        for g, p in sorted(member.eligible)
    }
    for g, group in enumerate(base.groups):
        for p in periods:
            for role, seats in group.seats.items():
                add(
                    seats * sorties[g, p]
                    <= pulp.lpSum(
                        z[i, g, p]
                        for i, member in enumerate(base.crew)
                        if member.role == role and (i, g, p) in z
                    ),
                    "crew",
                )
    for i, member in enumerate(base.crew):
        keys = [key for key in z if key[0] == i]
        for p in periods:
            add(
                pulp.lpSum(
                    overlap(max(1, base.groups[g].turnaround_hours), p, q) * z[i, g, q]
                    for _, g, q in keys
                    if q <= p and overlap(max(1, base.groups[g].turnaround_hours), p, q)
                )
                <= 1,
                "crew",
            )
        add(
            pulp.lpSum(
                max(1, base.groups[g].turnaround_hours) * z[i, g, q] for _, g, q in keys
            )
            <= member.duty_hours,
            "crew",
        )
    return model, hourly, labels


def solve(problem, options=None) -> Solution:
    if not problem.bases:
        return Solution({})
    try:
        import pulp
    except ImportError as exc:
        raise ToolInputError(
            "compute_sortie_capacity requires the backend opt extra (PuLP and HiGHS/CBC)"
        ) from exc
    seconds = options.time_limit_s if options else 8
    seed = options.seed if options else problem.seed
    deadline = monotonic() + seconds
    result = {}
    for base in problem.bases:
        if not base.groups:
            result[base.base_id] = BaseSolution(
                (0.0,) * base.horizon_hours, dict.fromkeys(FAMILIES, 0.0)
            )
            continue
        model, hourly, labels = build_model(base)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ToolInputError(
                "compute_sortie_capacity timed out; reduce horizon_hours or select fewer base_ids"
            )
        stats = model.solve(solver_factory(remaining, seed))
        status = int(stats.status) if hasattr(stats, "status") else int(stats)
        # PuLP 3 and 4 both use status 1 for optimal; 4 removed the old constants.
        if status != 1 or not model.valid(1e-5):
            raise ToolInputError(
                f"No optimal sortie-capacity LP for {base.base_id}; reduce horizon_hours or check scenario resources"
            )
        values = tuple(
            float(pulp.value(hourly[p]) or 0) for p in range(base.horizon_hours)
        )
        if any(not isfinite(v) or v < -1e-5 for v in values):
            raise RuntimeError(f"Invalid sortie-capacity solution for {base.base_id}")
        prices = dict.fromkeys(FAMILIES, 0.0)
        for constraint in constraints(model):
            family = labels.get(constraint.name)
            if family not in prices:
                continue
            dual = constraint.pi
            if dual is None or not isfinite(dual):
                raise RuntimeError(
                    f"Solver did not return finite LP duals for {base.base_id}"
                )
            prices[family] = max(prices[family], abs(float(dual)))
        result[base.base_id] = BaseSolution(tuple(max(0.0, v) for v in values), prices)
    return Solution(result)
