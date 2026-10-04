"""In-memory ports for tests and the local app."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from garuda.contracts.plan import PlanDraft
from garuda.schemas.mission import GeneratedScenario


class InMemoryScenarioSource:
    def __init__(self, scenario: GeneratedScenario, *, as_of: datetime | None = None) -> None:
        self._scenario = scenario
        if as_of is not None:
            self._as_of = as_of
        elif scenario.cells:
            self._as_of = max(cell.time for cell in scenario.cells)
        else:
            self._as_of = datetime.now(timezone.utc)

    def snapshot(self) -> GeneratedScenario:
        return self._scenario

    def base_ids(self) -> list[str]:
        return [base.base_id for base in self._scenario.bases]

    def data_as_of(self) -> datetime:
        return self._as_of


class InMemoryPlanStore:
    def __init__(self) -> None:
        self._plans: dict[str, PlanDraft] = {}

    def get(self, plan_id: str) -> PlanDraft:
        try:
            return self._plans[plan_id]
        except KeyError:
            raise KeyError(f"unknown plan {plan_id}") from None

    def put(self, plan: PlanDraft) -> str:
        self._plans[plan.plan_id] = plan
        return plan.plan_id


class LocalModelStore:
    """Joblib artifacts under ``root/<name>/<version>.joblib``."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def load(self, name: str, version: str | None = None) -> object:
        path = self._artifact_path(name, version)
        if not path.is_file():
            label = name if version is None else f"{name}@{version}"
            raise FileNotFoundError(f"no saved model {label}")
        return _joblib().load(path)

    def save(self, name: str, artifact: object, metadata: dict) -> str:
        version = str(metadata.get("version") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
        folder = self.root / _safe_name(name)
        folder.mkdir(parents=True, exist_ok=True)
        _joblib().dump(artifact, folder / f"{version}.joblib")
        (folder / f"{version}.json").write_text(json.dumps(metadata, default=str))
        return version

    def _artifact_path(self, name: str, version: str | None) -> Path:
        folder = self.root / _safe_name(name)
        if version is not None:
            return folder / f"{_safe_name(version)}.joblib"
        saved = sorted(folder.glob("*.joblib")) if folder.is_dir() else []
        if not saved:
            raise FileNotFoundError(f"no saved model {name}")
        return saved[-1]


def _safe_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"invalid model name {name!r}")
    return cleaned


def _joblib() -> Any:
    try:
        import joblib
    except ImportError as exc:
        raise RuntimeError("joblib is not installed; install the ml extra to load models") from exc
    return joblib
