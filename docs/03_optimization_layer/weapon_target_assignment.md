# Resource Allocation Optimization

Tool: `assign_weapons_to_targets`

> Note: The tool and schema names are legacy API identifiers and are preserved for compatibility. The optimization itself is described here as a general resource-allocation problem.

## Problem

Allocate limited resource units across a set of objectives to maximize expected total value, accounting for diminishing returns when multiple units are assigned to the same objective, while respecting inventory and capacity constraints.

## Formulation

**Sets**:

- `W`: resource types
- `T`: objectives

**Parameters**:

- `V_t`: value or priority of objective `t`
- `p_{k,t}`: probability that one unit of resource type `k` successfully contributes to objective `t`
- `N_k`: available quantity of resource type `k`
- `L_a`: capacity associated with resource group or allocation source `a`

**Variables**:

```text
x_{k,t} ∈ Z≥0
```

Number of units of resource type `k` allocated to objective `t`.

## Objective

Let:

```text
S_t = Π_k (1 − p_{k,t})^{x_{k,t}}
```

represent the probability that objective `t` remains unsatisfied after all allocated resources are considered.

Minimize expected remaining value:

```text
min Σ_t V_t · S_t
```

Equivalently, maximize expected achieved value:

```text
max Σ_t V_t · (1 − S_t)
```

or:

```text
max Σ_t V_t ·
    (1 − Π_k (1 − p_{k,t})^{x_{k,t}})
```

This preserves diminishing returns: additional units can improve the expected outcome, but their marginal benefit decreases as more resources are assigned to the same objective.

## Constraints

### 1. Inventory

Total allocation of each resource type cannot exceed available inventory:

```text
Σ_t x_{k,t} ≤ N_k
```

### 2. Capacity

Allocations associated with a particular source or resource group must remain within its capacity:

```text
Σ_{k,t via a} x_{k,t} ≤ L_a
```

### 3. Per-objective allocation limit

```text
x_{k,t} ≤ x_max
```

### 4. Optional minimum service level

For objectives that require a minimum probability of success:

```text
1 − S_t ≥ θ_t
```

where `θ_t` is the required minimum success level for objective `t`.

## Linearisation

The nonlinear probability term can be transformed using:

```text
log S_t =
Σ_k x_{k,t} · log(1 − p_{k,t})
```

Since:

```text
S_t = exp(·)
```

is convex in the transformed variable, use a piecewise-linear approximation.

Represent the integer allocation as:

```text
x_{k,t} = Σ_j j · b_{k,t,j}
```

where:

```text
j = 0..J
J ≈ 4
```

with SOS1 over `j`.

Define:

```text
u_t =
Σ_k x_{k,t} ln(1 − p_{k,t})
```

and approximate:

```text
S_t = exp(u_t)
```

using a piecewise-linear lambda formulation with SOS2.

The resulting problem can then be solved as a MILP.

## Fast Heuristic

For faster execution, use greedy marginal return.

At each step, select the resource-objective pair with the highest incremental expected value per unit cost:

```text
V_t · S_t · p_{k,t} / cost[k]
```

Because each additional allocation provides diminishing marginal benefit, this provides an efficient approximation for the allocation problem.

After the initial greedy allocation, apply local swap improvement.

## Pseudocode

```text
S_t = 1 for all objectives

while any inventory remains:

    best =
        argmax_{k,t}
        V_t * S_t * p[k,t] / cost[k]

    if gain < eps:
        break

    x[best]++
    S_t *= (1 - p[best])
    stock[k]--

local_search(swap pairs)
```

## Tool Signature

The existing public API must remain unchanged:

```python
class WTAInput(BaseModel):
    target_ids: list[str]
    base_ids: list[str] | None = None
    method: Literal["greedy", "milp"] = "greedy"
    min_destroy_prob: dict[str, float] = {}
    scenario_time: datetime


class WTAOutput(BaseModel):
    allocations: list[WeaponAllocation]
    expected_destroyed_value: float
    kill_prob_by_target: dict[str, float]
    stock_remaining: dict[str, int]
```

Names such as `target_ids`, `WeaponAllocation`, `min_destroy_prob`, `kill_prob_by_target`, and `expected_destroyed_value` are retained only because they are part of the existing public contract.

They should not be reinterpreted, renamed, or extended during implementation.

## Data Inputs

The implementation uses the existing repository fields:

- resource-effectiveness probabilities
- available stock
- objective metadata
- environmental adjustment factors
- operating-condition adjustments
- source or capacity information

Existing schema names remain unchanged in code.

## Complexity

Greedy approach:

```text
O(n log n)
```

The MILP formulation with piecewise-linear terms is intended to remain practical for approximately:

```text
50 objectives × 14 resource types
```

## Failure Modes

### Insufficient inventory

If available resources cannot satisfy requested minimum service levels, report the affected objectives using the existing output structures.

Do not fabricate additional inventory or silently relax requirements.

### Uncertain effectiveness estimates

Where already supported by the repository design, a conservative lower-confidence estimate may be used.

Do not invent uncertainty models that are not defined by the existing implementation or specification.

### Unsupported combinations

If a resource type is not compatible with an objective according to the existing data, exclude that allocation from the candidate set.

Do not infer compatibility that is not represented in the current schemas or data.

## Validation

Validate the implementation using:

- deterministic small instances with known solutions
- greedy versus MILP comparison
- inventory-limit checks
- capacity-limit checks
- minimum-service-level checks
- unsupported resource-objective combinations
- repeated stochastic evaluation of the combined success probabilities

The implementation must preserve the existing optimization logic and public contracts without introducing new domain assumptions.
