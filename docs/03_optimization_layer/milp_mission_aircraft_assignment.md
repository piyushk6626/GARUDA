# MILP Aircraft-Task Assignment

Tool: `assign_missions`

## Problem

Given tasks with priorities, time windows and capability needs, and aircraft with availability, location, configuration options and crew, choose which aircraft, with which configuration, are assigned to which tasks to maximise priority-weighted value while respecting payload/resource availability, range, and base sortie limits.

## Formulation

**Sets**:  
M tasks, A aircraft, C configurations, T time buckets (6 h), B bases.

**Parameters**:

- `v_m` value, based on task priority points
- `req_m` aircraft required, minimum `n_m^min`, maximum `n_m^max`
- `ok_{a,c,m}` = 1 if aircraft `a` with configuration `c` is capable of task `m`, considering capability, range including refueling support, and weather go/no-go
- `avail_{a,t}` aircraft availability, including current serviceability and forecast availability
- `w_{c,k}` quantity of resource/payload type `k` required by configuration `c`
- `S_{b,k}` available stock of resource/payload type `k` at base `b`
- `cap_{b,t}` sortie capacity at base `b` during time bucket `t`
- `pk_{c,m}` estimated effectiveness probability for configuration `c` on task `m`
- `r_{a,m}` operational risk associated with aircraft `a` performing task `m`
- `λ` risk weight

**Variables**:

`x_{a,c,m,t} ∈ {0,1}` assignment variable

`y_m ∈ {0,1}` task fulfilled

## Objective

```text
max Σ_m v_m · y_m
    + Σ x · μ · pk_{c,m}
    − λ Σ x · r_{a,m}
```

The objective maximises priority-weighted task completion and expected effectiveness while penalising operational risk.

## Constraints

1. **Aircraft turnaround**

   Each aircraft may perform at most one sortie within its turnaround window:

   ```text
   Σ_{c,m,t' ∈ W(t)} x_{a,c,m,t'} ≤ avail_{a,t}
   ```

2. **Capability**

   An assignment can only be made when the selected aircraft and configuration can satisfy the task:

   ```text
   x_{a,c,m,t} ≤ ok_{a,c,m}
   ```

3. **Required aircraft**

   Assigned aircraft must remain within the minimum and maximum requirement for a fulfilled task:

   ```text
   n_m^min · y_m
   ≤
   Σ_{a,c,t} x_{a,c,m,t}
   ≤
   n_m^max · y_m
   ```

4. **Payload/resource stock**

   Configuration resource consumption must not exceed usable stock at the originating base:

   ```text
   Σ_{a,c,m,t: base(a)=b} w_{c,k} x
   ≤
   S_{b,k} − reserved
   ```

5. **Base sortie capacity**

   The number of aircraft departing a base during a time bucket must remain within available capacity:

   ```text
   Σ_{a: base(a)=b} Σ x_{·,t}
   ≤
   cap_{b,t}
   ```

6. **Time window**

   `x` variables are created only for time buckets that are feasible given departure time, travel duration, and required arrival/task time.

7. **Task dependencies**

   Where one task depends on another task being completed:

   ```text
   y_{dependent} ≤ y_{prerequisite}
   ```

8. **Crew link**

   Aircraft assignments must not exceed the number of suitable available crew:

   ```text
   Σ x ≤ crew_available
   ```

   Alternatively, this may be linked to the result of `schedule_crew`.

9. **Refueling/support requirement**

   A task requiring additional in-flight support may only be fulfilled when the required support is available:

   ```text
   y_task ≤ y_support
   ```

## Data Inputs

- `aircraft`
- `crew`
- `resource_stock`
- `load_config`
- `task`
- `weather`, including go/no-go conditions
- `operational_risk`
- `base`, including capacity

## Library

PuLP with HiGHS/CBC, or OR-Tools `pywraplp` using SCIP/CBC.

Preprocess the candidate assignments to remove infeasible tuples.

Worst-case variable count:

```text
150 × 5 × 50 × 12 = 450k
```

After capability, availability, time-window, range, configuration and resource pruning, this should typically reduce to approximately:

```text
30k–60k variables
```

## Pseudocode

```text
build ok-tuples by filtering
create variables only for ok-tuples
add objective and constraints
solve(time_limit, gap=1%)
extract assignments
compute unassigned reasons via LP relaxation duals /
per-task infeasibility checks
```

## Tool Signature

```python
class AssignMissionsInput(BaseModel):
    scenario_time: datetime
    horizon_hours: int = 72
    mission_ids: list[str] | None = None
    weights: ObjectiveWeights = ObjectiveWeights(
        risk=0.2,
        effect=0.3
    )
    locked_assignments: list[Assignment] = []
    time_limit_s: int = 30


class AssignMissionsOutput(BaseModel):
    plan_id: str
    status: Literal[
        "OPTIMAL",
        "FEASIBLE",
        "INFEASIBLE"
    ]
    assignments: list[Assignment]
    unassigned: list[UnassignedMission]
    kpis: PlanKPIs
    binding_constraints: list[str]
```

Existing public class names and field names remain unchanged for compatibility even where their names contain legacy terminology.

## Complexity

NP-hard.

Practical for fleet-scale scheduling through aggressive preprocessing and candidate pruning, typically solving within seconds to under one minute depending on scenario size.

Use gap-limited optimization where appropriate.

## Explainability

Provide:

- value contribution for each task
- reasons for unassigned tasks
- important resource limitations
- binding base-capacity constraints
- configuration limitations
- support-capacity limitations
- sensitivity information
- shadow prices from the LP relaxation where available

Example binding constraints might include:

```text
resource stock at B2
base departure capacity
crew availability
support availability
aircraft turnaround
```

## Failure Modes

**Infeasible**

Identify the constraints preventing a feasible assignment. Where supported by the existing implementation, analyse relaxation of minimum aircraft requirements without silently altering the actual problem.

**Timeout**

Return the best valid incumbent available within the existing output contract, together with the applicable solver status and gap information where the existing schemas permit it.

**Stale data**

Identify stale input data through the existing GARUDA context/envelope mechanisms rather than inventing additional public output fields.

## Validation

Use the independent plan validator.

Compare optimized output against the greedy baseline.

Include unit tests using small deterministic instances with known optimal solutions.
