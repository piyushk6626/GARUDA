try:
    # ortools must load its bundled HiGHS before pulp pulls in highspy's libhighs;
    # the reverse order fails to resolve symbols when ortools is imported later.
    import ortools.constraint_solver.pywrapcp  # noqa: F401
except ImportError:
    pass
