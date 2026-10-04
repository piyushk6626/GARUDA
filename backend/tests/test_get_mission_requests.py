"""Mission filters, deterministic priority ordering, and time-window boundaries."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from garuda.contracts.geo import TimeWindow
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.schemas.mission import Mission
from garuda.tools.data.get_mission_requests import GetMissionRequestsInput, get_mission_requests

WHEN = datetime(2026, 6, 1, 6, tzinfo=timezone.utc)


def _mission(mission_id="M-TEST", **overrides):
    return Mission.model_validate(
        {
            "mission_id": mission_id,
            "type": "STRIKE",
            "priority": 2,
            "value": 60,
            "requested_by": "TEST",
            "window_start": WHEN,
            "window_end": WHEN + timedelta(hours=2),
            "duration_min": 30,
            "target_id": "T-TEST",
            "area_wkt": None,
            "required_capabilities": ["AG_PGM"],
            "min_aircraft": 1,
            "max_aircraft": 2,
            "required_effect": "DESTROY",
            "min_pk": 0.8,
            "status": "REQUESTED",
            "dependencies": [],
            **overrides,
        }
    )


def _context(missions):
    snapshot = SimpleNamespace(missions=missions)
    blank = SimpleNamespace()
    return ToolContext(
        scenario=SimpleNamespace(snapshot=lambda: snapshot),
        plans=blank,
        models=blank,
        providers=blank,
        scenario_time=WHEN,
    )


def _run(missions, **filters):
    payload = GetMissionRequestsInput(**filters)
    return asyncio.run(get_mission_requests(payload, _context(missions)))


def _ids(output):
    assert output.total == len(output.items)
    return [item.mission_id for item in output.items]


def test_unfiltered_query_orders_by_priority_then_id_and_projects_only_item_fields():
    missions = [
        _mission("Z", priority=2, value=1000),
        _mission("LOW", priority=5),
        _mission("A", priority=2, window_start=WHEN - timedelta(hours=2)),
        _mission("HIGH", priority=1, value=1, status="DONE"),
    ]
    before = [mission.model_dump() for mission in missions]

    output = _run(missions)

    assert _ids(output) == ["HIGH", "A", "Z", "LOW"]
    assert output.items[0].model_dump() == {
        "mission_id": "HIGH", "type": "STRIKE", "status": "DONE", "priority": 1,
    }
    assert [mission.model_dump() for mission in missions] == before
    assert _run(list(reversed(missions))) == output


@pytest.mark.parametrize(
    "statuses,expected",
    [
        (["REQUESTED"], ["REQUESTED"]),
        (["PLANNED", "APPROVED"], ["APPROVED", "PLANNED"]),
        (["DONE", "CANCELLED"], ["CANCELLED", "DONE"]),
        ([], []),
        (["UNKNOWN"], []),
    ],
)
def test_status_filter(statuses, expected):
    missions = [_mission(status, status=status) for status in (
        "REQUESTED", "PLANNED", "APPROVED", "EXECUTING", "DONE", "CANCELLED",
    )]
    assert _ids(_run(missions, status=statuses)) == expected


@pytest.mark.parametrize(
    "types,expected",
    [(["STRIKE"], ["STRIKE"]), (["CAS", "ISR"], ["CAS", "ISR"]), ([], []), (["UNKNOWN"], [])],
)
def test_types_filter(types, expected):
    missions = [_mission(kind, type=kind) for kind in ("STRIKE", "CAS", "ISR")]
    assert _ids(_run(missions, types=types)) == expected


@pytest.mark.parametrize(
    "priority_max,expected",
    [(1, ["P1"]), (2, ["P1", "P2"]), (5, ["P1", "P2", "P3", "P4", "P5"]), (0, [])],
)
def test_priority_max_keeps_higher_priorities_and_includes_threshold(priority_max, expected):
    missions = [_mission(f"P{priority}", priority=priority) for priority in range(5, 0, -1)]
    assert _ids(_run(missions, priority_max=priority_max)) == expected


@pytest.mark.parametrize(
    "start_hours,end_hours,expected",
    [
        (-2, -1, False),  # entirely before
        (-1, 0, False),  # ends exactly at the query start
        (-1, 1, True),   # overlaps the query start
        (0, 2, True),    # equal windows
        (0, 1, True),    # shares the query start
        (0.5, 1.5, True),  # contained by the query
        (-1, 3, True),   # contains the query
        (1, 2, True),    # shares the query end
        (1, 3, True),    # overlaps the query end
        (2, 3, False),   # starts exactly at the query end
        (3, 4, False),   # entirely after
    ],
)
def test_window_requires_positive_duration_overlap(start_hours, end_hours, expected):
    mission = _mission(
        window_start=WHEN + timedelta(hours=start_hours),
        window_end=WHEN + timedelta(hours=end_hours),
    )
    window = TimeWindow(start=WHEN, end=WHEN + timedelta(hours=2))

    assert _ids(_run([mission], window=window)) == (["M-TEST"] if expected else [])


@pytest.mark.parametrize("side", ["start", "end"])
def test_one_microsecond_of_overlap_is_included(side):
    if side == "start":
        start, end = WHEN - timedelta(hours=1), WHEN + timedelta(microseconds=1)
    else:
        start = WHEN + timedelta(hours=2, microseconds=-1)
        end = WHEN + timedelta(hours=3)
    mission = _mission(window_start=start, window_end=end)
    window = TimeWindow(start=WHEN, end=WHEN + timedelta(hours=2))
    assert _ids(_run([mission], window=window)) == ["M-TEST"]


def test_all_filters_are_combined_and_do_not_modify_snapshot():
    missions = [
        _mission("REQUEST", priority=1),
        _mission("PLAN", type="CAS", status="PLANNED"),
        _mission("STATUS", status="DONE"),
        _mission("TYPE", type="ISR"),
        _mission("PRIORITY", priority=3),
        _mission("WINDOW", window_start=WHEN + timedelta(hours=2), window_end=WHEN + timedelta(hours=3)),
    ]
    before = [mission.model_dump() for mission in missions]

    output = _run(
        missions,
        status=["REQUESTED", "PLANNED"],
        types=["STRIKE", "CAS"],
        priority_max=2,
        window=TimeWindow(start=WHEN, end=WHEN + timedelta(hours=2)),
    )

    assert _ids(output) == ["REQUEST", "PLAN"]
    assert [mission.model_dump() for mission in missions] == before


def test_timezone_offsets_are_compared_as_instants():
    offset = timezone(timedelta(hours=5, minutes=30))
    window = TimeWindow(
        start=WHEN.astimezone(offset),
        end=(WHEN + timedelta(hours=2)).astimezone(offset),
    )
    missions = [
        _mission("IN"),
        _mission("TOUCH", window_start=WHEN + timedelta(hours=2), window_end=WHEN + timedelta(hours=3)),
    ]
    assert _ids(_run(missions, window=window)) == ["IN"]


def test_mismatched_timezone_awareness_is_an_actionable_input_error():
    start = WHEN.replace(tzinfo=None)
    window = TimeWindow(start=start, end=start + timedelta(hours=2))
    with pytest.raises(ToolInputError, match="timezone awareness"):
        _run([_mission()], window=window)


def test_naive_times_work_when_both_scenario_and_query_use_them():
    start = WHEN.replace(tzinfo=None)
    mission = _mission(window_start=start, window_end=start + timedelta(hours=2))
    window = TimeWindow(start=start, end=start + timedelta(hours=1))
    assert _ids(_run([mission], window=window)) == ["M-TEST"]


def test_empty_snapshot_returns_no_fabricated_missions():
    assert _run([]).model_dump() == {"total": 0, "items": []}


def test_each_call_reads_the_current_scenario_snapshot():
    missions = [_mission("FIRST")]
    ctx = _context(missions)
    payload = GetMissionRequestsInput()
    assert _ids(asyncio.run(get_mission_requests(payload, ctx))) == ["FIRST"]
    missions[:] = [_mission("SECOND")]
    assert _ids(asyncio.run(get_mission_requests(payload, ctx))) == ["SECOND"]


def test_retrieval_adapter_executes_the_tool_with_serialized_window():
    from garuda.harness.retrieval.tools import run_data_call

    ctx = _context([_mission("KEEP"), _mission("DROP", priority=5)])
    record = asyncio.run(run_data_call(
        "get_mission_requests",
        {
            "status": ["REQUESTED"],
            "types": ["STRIKE"],
            "priority_max": 2,
            "window": {"start": WHEN.isoformat(), "end": (WHEN + timedelta(hours=1)).isoformat()},
        },
        ctx,
    ))

    assert record.ok is True
    assert record.data == {
        "total": 1,
        "items": [{"mission_id": "KEEP", "type": "STRIKE", "status": "REQUESTED", "priority": 2}],
    }
