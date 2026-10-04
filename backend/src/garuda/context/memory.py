"""In-memory ports for tests and the prototype. TODO(phase1): implement."""


class InMemoryScenarioSource:
    def __init__(self, scenario) -> None:
        raise NotImplementedError


class InMemoryPlanStore:
    def __init__(self) -> None:
        raise NotImplementedError


class LocalModelStore:
    def __init__(self, root) -> None:
        raise NotImplementedError
