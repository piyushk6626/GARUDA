"""Evaluate the shared greedy assignment as a feasible comparison point."""


def baseline(problem):
    from garuda.optimization.assign_missions.baseline import baseline as assign
    from garuda.optimization.pareto_tradeoffs.model import Point, evaluate

    solution = assign(problem.assignment)
    if solution is None:
        return None
    return Point(
        solution.selected,
        solution.crew,
        evaluate(problem, solution.selected, solution.crew),
    )
