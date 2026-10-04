"""Bounded A*, Theta*, and seeded RRT* searches on a local metric grid."""

import heapq
import itertools
import math
import time
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
from shapely.geometry import LineString, Point

from garuda.contracts.tool import ToolInputError
from garuda.optimization.plan_route.problem import GEOD

MAX_CELLS = 40_000
SEARCH_SECONDS = 16.0
CLEARANCE_FT = 1000


@dataclass(frozen=True)
class Edge:
    distance: float
    cost: float
    mask: int
    peak_risk: float
    zones: tuple[str, ...]


@dataclass(frozen=True)
class Route:
    points: tuple[tuple[float, float], ...]
    altitude: int
    distance: float
    cost: float
    mask: int
    peak_risk: float
    zones: tuple[str, ...]


@dataclass(frozen=True)
class Solution:
    route: Route
    alternatives: tuple[tuple[str, Route], ...]
    risk_cap_exceeded: bool = False


class Surface:
    """Integrate whole edges; endpoint-only checks would tunnel through obstacles.

    Threats are frozen at departure. Airspace windows and forecast buckets are
    evaluated at arrival using distance, never the weighted search objective.
    """

    def __init__(self, problem, altitude, weights, enforce_risk=True):
        self.p = problem
        self.altitude = altitude
        self.weights = weights
        self.enforce_risk = enforce_risk
        self.speed = problem.aircraft.cruise_kts * 1.852
        self.sources = [(i, t) for i, t in enumerate(problem.threats)
                        if t.min_alt_ft <= altitude <= t.max_alt_ft]
        self.zones = [(z, g) for z, g in problem.zones
                      if z.floor_ft <= altitude <= z.ceiling_ft]
        self.weather_cache = {}
        if problem.elevation is None:
            terrain_floor = max(0.0, problem.terrain.elev_max_m) / max(problem.terrain.elev_max_m, 1)
        elif problem.terrain_penalty is not None:
            terrain_floor = float(problem.terrain_penalty.min())
        else:
            terrain_floor = max(0.0, float(problem.elevation.min())) / max(problem.terrain.elev_max_m, 1)
        self.min_factor = 1 + weights.terrain * terrain_floor

    def loss(self, mask):
        # Independent engagement once per distinct threat, not once per grid cell.
        return sum(-math.log1p(-min(t.probability, 1 - 1e-15))
                   for i, t in enumerate(self.p.threats) if mask & (1 << i))

    def _weather(self, lat, lon, at):
        if not self.p.weather:
            return 0.0
        # The weather grid is coarse (~0.5 degrees); nearest cells represent it.
        key = (lat, lon)
        if key not in self.weather_cache:
            locations = self.p.weather_locations
            dsq = (locations[:, 0] - lat) ** 2 + (
                (locations[:, 1] - lon) * math.cos(math.radians(lat))) ** 2
            location = locations[int(np.argmin(dsq))]
            self.weather_cache[key] = (None if float(np.min(dsq)) * 111.2 ** 2 > 45 ** 2
                                       else self.p.weather[tuple(location)])
        forecasts = self.weather_cache[key]
        if forecasts is None:
            return None
        moment, cell = min(forecasts, key=lambda row: abs((row[0] - at).total_seconds()))
        if abs((moment - at).total_seconds()) > 3600:
            return None
        icing = (cell.freezing_level_ft <= self.altitude <= cell.freezing_level_ft + 4000
                 and cell.cloud_low_pct + cell.cloud_mid_pct + cell.cloud_high_pct >= 50)
        return 1 - (0.0 if icing else cell.flyability)

    def _airspace(self, a, b, distance, travelled):
        path = Point(a) if distance < 1e-10 else LineString((a, b))
        crossed, penalty = [], 0.0
        for zone, geom in self.zones:
            if not geom.intersects(path):
                continue
            # Clip the flight segment to the zone's exact active time interval.
            lo, hi = 0.0, 1.0
            if zone.active_from is not None:
                start = self.p.payload.depart_time + timedelta(hours=travelled / self.speed)
                duration = distance / self.speed * 3600
                if duration < 1e-10:
                    if not zone.active_from <= start < zone.active_to:
                        continue
                else:
                    lo = max(0.0, (zone.active_from - start).total_seconds() / duration)
                    hi = min(1.0, (zone.active_to - start).total_seconds() / duration)
                    if lo > hi or hi <= 0 or lo >= 1:
                        # Activation exactly at endpoint still applies there.
                        end = start + timedelta(seconds=duration)
                        if not (zone.active_from <= end < zone.active_to and geom.covers(Point(b))):
                            continue
                        lo = hi = 1.0
                    # active_to is exclusive, including a tangency at deactivation.
                    if hi < 1.0:
                        hi = np.nextafter(hi, -np.inf)
                    if hi < lo:
                        continue
            delta = np.asarray(b) - np.asarray(a)
            clipped = (np.asarray(a) + lo * delta, np.asarray(a) + hi * delta)
            part = Point(clipped[0]) if hi == lo else LineString(clipped)
            intersection = geom.intersection(part)
            if intersection.is_empty:
                continue
            if zone.type == "PROHIBITED" or zone.capacity_per_hour == 0:
                return None
            crossed.append(zone.zone_id)
            # All permitted controlled/restricted zones add a civil penalty.
            penalty += intersection.length / max(distance, 1e-10)
        return crossed, penalty

    def edge(self, a, b, travelled):
        a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
        ll = self.p.latlon((a, b))
        distance = abs(GEOD.inv(ll[0, 1], ll[0, 0], ll[1, 1], ll[1, 0])[2]) / 1000
        if travelled + distance > self.p.aircraft.range_km + 1e-8:
            return None
        airspace = self._airspace(a, b, distance, travelled)
        if airspace is None:
            return None
        crossed, civil = airspace
        delta = b - a
        length2 = float(delta @ delta)
        intervals, fractions = [], {0.0, 1.0}
        path = None
        for i, threat in self.sources:
            v = a - (threat.x, threat.y)
            if length2 < 1e-20:
                inside = threat.geometry.covers(Point(a))
                if inside:
                    intervals.append((i, threat, 0.0, 1.0))
                continue
            center = -float(v @ delta) / length2
            discriminant = center ** 2 - (float(v @ v) - threat.radius ** 2) / length2
            if discriminant < 0:
                continue
            root = math.sqrt(max(0, discriminant))
            lo, hi = max(0.0, center - root), min(1.0, center + root)
            if lo <= hi:
                # The enclosing disk is only a fast filter. Cost/risk use the
                # actual projected geodesic envelope, including thin crossings.
                if path is None:
                    path = LineString((a, b))
                intersection = threat.geometry.intersection(path)
                parts = list(intersection.geoms) if hasattr(intersection, "geoms") else [intersection]
                for part in parts:
                    if part.is_empty:
                        continue
                    coords = list(part.coords)
                    positions = [float((np.asarray(xy) - a) @ delta) / length2 for xy in coords]
                    start, end = max(0.0, min(positions)), min(1.0, max(positions))
                    intervals.append((i, threat, start, end))
                    fractions.update((start, end))
        mask = 0
        for i, _threat, _lo, _hi in intervals:
            mask |= 1 << i
        events = sorted(fractions)
        probes = events + [(lo + hi) / 2 for lo, hi in itertools.pairwise(events)]
        peak = average = 0.0
        for fraction in probes:
            active = {i: threat.probability for i, threat, lo, hi in intervals
                      if lo - 1e-12 <= fraction <= hi + 1e-12}
            survival = math.prod(1 - probability for probability in active.values())
            peak = max(peak, 1 - survival)
        for lo, hi in itertools.pairwise(events):
            middle = (lo + hi) / 2
            active = {i: threat.probability for i, threat, start, end in intervals
                      if start <= middle <= end}
            risk = 1 - math.prod(1 - probability for probability in active.values())
            average += (hi - lo) * risk
        if self.enforce_risk and peak > 0 and peak >= self.p.payload.max_risk:
            return None

        # At most 500 m between checks. Check neighbouring DEM cells too, so a
        # diagonal shortcut cannot miss a narrow ridge at a cell corner.
        spacing = min(.5, self.p.terrain.cell_km / 2)
        count = max(1, math.ceil(distance / spacing))
        samples = a + np.linspace(0, 1, count + 1)[:, None] * delta
        latlon = self.p.latlon(samples)
        meta = self.p.terrain
        lat, lon = latlon[:, 0], latlon[:, 1]
        if np.any((lat < meta.lat_min) | (lat > meta.lat_max)
                  | (lon < meta.lon_min) | (lon > meta.lon_max)):
            return None  # Unknown terrain is not assumed to be flat.
        if self.p.elevation is None:
            elevations = np.full(len(samples), meta.elev_max_m)
            penalties = np.maximum(0, elevations) / max(meta.elev_max_m, 1)
        else:
            row = np.clip(((lat - meta.lat_min) / (meta.lat_max - meta.lat_min) * meta.rows).astype(int), 0, meta.rows - 1)
            col = np.clip(((lon - meta.lon_min) / (meta.lon_max - meta.lon_min) * meta.cols).astype(int), 0, meta.cols - 1)
            elevations = np.zeros(len(samples))
            for dr, dc in itertools.product((-1, 0, 1), repeat=2):
                elevations = np.maximum(elevations, self.p.elevation[
                    np.clip(row + dr, 0, meta.rows - 1), np.clip(col + dc, 0, meta.cols - 1)])
            if self.p.terrain_penalty is None:
                penalties = np.maximum(0, elevations) / max(meta.elev_max_m, 1)
            else:
                penalties = self.p.terrain_penalty[row, col]
        if np.any(elevations / .3048 + CLEARANCE_FT > self.altitude):
            return None
        weather = []
        for fraction, (la, lo) in zip(np.linspace(0, 1, count + 1), latlon):
            at = self.p.payload.depart_time + timedelta(hours=(travelled + fraction * distance) / self.speed)
            value = self._weather(float(la), float(lo), at)
            if value is None:
                return None
            weather.append(value)
        if len(self.p.traffic):
            relevant = self.p.traffic[(np.isnan(self.p.traffic[:, 2]))
                                      | (abs(self.p.traffic[:, 2] - self.altitude) <= 2000)]
            if len(relevant):
                density = np.exp(-np.sum((samples[:, None, :] - relevant[None, :, :2]) ** 2, axis=2) / 50)
                civil += float(np.mean(np.sum(density, axis=1)))
        w = self.weights
        cost = distance * (1 + w.threat * average + w.weather * float(np.mean(weather))
                           + w.civil * civil + w.terrain * float(np.mean(penalties)))
        return Edge(distance, cost, mask, peak, tuple(crossed))


def assess(surface, points):
    distance = cost = peak = 0.0
    mask, zones = 0, set()
    if len(points) == 1:
        pairs = [(points[0], points[0])]
    else:
        pairs = itertools.pairwise(points)
    for a, b in pairs:
        edge = surface.edge(a, b, distance)
        if edge is None:
            return None
        distance += edge.distance
        cost += edge.cost
        mask |= edge.mask
        peak = max(peak, edge.peak_risk)
        zones.update(edge.zones)
    return Route(tuple(tuple(map(float, xy)) for xy in points), surface.altitude,
                 distance, cost, mask, peak, tuple(sorted(zones)))


@dataclass(frozen=True)
class Label:
    node: tuple[int, int] | None
    point: tuple[float, float]
    distance: float
    cost: float
    mask: int
    parent: int | None


def _grid_search(surface, deadline, *, theta=False, safest=False):
    p = surface.p
    direct = assess(surface, [(0.0, 0.0), p.goal] if p.goal != (0.0, 0.0) else [(0.0, 0.0)])
    mandatory = 0
    for i, threat in surface.sources:
        if any(threat.geometry.covers(Point(point)) for point in ((0.0, 0.0), p.goal)):
            mandatory |= 1 << i
    if direct is not None and ((safest and direct.mask == mandatory)
                               or (not safest and abs(direct.cost - direct.distance * surface.min_factor) < 1e-8)):
        return direct
    if p.goal == (0.0, 0.0):
        return direct
    # Search the range-feasible ellipse's bounding square; bounded resolution
    # keeps large aircraft ranges from creating an unbounded Python search.
    half = p.aircraft.range_km / 2 * 1.02
    midpoint = np.asarray(p.goal) / 2
    step = max(2.0, (2 * half) / (math.sqrt(MAX_CELLS) - 3))
    xmin, xmax = math.floor((midpoint[0] - half) / step), math.ceil((midpoint[0] + half) / step)
    ymin, ymax = math.floor((midpoint[1] - half) / step), math.ceil((midpoint[1] + half) / step)
    labels = [Label((0, 0), (0.0, 0.0), 0.0, 0.0, 0, None)]
    serial = itertools.count()
    queue = []
    frontier = {}
    speed = surface.speed
    incumbent = direct

    # Supply quick feasible detours before expensive weighted expansion. Large
    # weather penalties can otherwise exhaust the budget while the admissible
    # distance heuristic explores a broad low-cost region near the origin.
    direction = np.asarray(p.goal)
    norm = float(np.linalg.norm(direction))
    perpendicular = np.asarray((-direction[1], direction[0])) / norm
    for factor, sign in itertools.product((.1, .3, .6), (-1, 1)):
        if time.monotonic() >= deadline:
            break
        shift = perpendicular * max(step, norm * factor) * sign
        candidate = assess(surface, [(0.0, 0.0), tuple(shift), tuple(direction + shift), p.goal])
        if candidate is not None:
            value = (surface.loss(candidate.mask), candidate.distance) if safest else (candidate.cost, 0.0)
            old = ((surface.loss(incumbent.mask), incumbent.distance) if safest else (incumbent.cost, 0.0)) if incumbent else None
            if old is None or value < old:
                incumbent = candidate

    def metric(label):
        return (surface.loss(label.mask), label.distance) if safest else (label.cost, 0.0)

    def remaining(point):
        ll = p.latlon((point, p.goal))
        return abs(GEOD.inv(ll[0, 1], ll[0, 0], ll[1, 1], ll[1, 0])[2]) / 1000

    def push(index):
        label = labels[index]
        value = metric(label)
        rem = remaining(label.point)
        if label.distance + rem > p.aircraft.range_km + 1e-7:
            return
        priority = (value[0], value[1] + rem) if safest else (value[0] + rem * surface.min_factor, 0.0)
        if incumbent is not None:
            bound = (surface.loss(incumbent.mask), incumbent.distance) if safest else (incumbent.cost, 0.0)
            if priority >= bound:
                return
        # Retain Pareto labels for cost and travelled distance. Weather is
        # time-dependent; a five-minute bucket is part of the search state.
        key = (label.node, int(label.distance / speed * 12), label.mask if safest else 0)
        rows = frontier.setdefault(key, [])
        if any(metric(labels[j]) <= value and labels[j].distance <= label.distance + 1e-9 for j in rows):
            return
        rows[:] = [j for j in rows if not (value <= metric(labels[j]) and label.distance <= labels[j].distance + 1e-9)]
        rows.append(index)
        heapq.heappush(queue, (*priority, next(serial), index, key))

    push(0)
    while queue and time.monotonic() < deadline:
        _f, _secondary, _seq, index, key = heapq.heappop(queue)
        if index not in frontier.get(key, ()):
            continue
        current = labels[index]
        if current.node is None:
            indices = []
            while index is not None:
                indices.append(labels[index].point)
                index = labels[index].parent
            candidate = assess(surface, list(reversed(indices)))
            if candidate is not None:
                incumbent = candidate
                break
        x, y = current.node
        neighbours = [((x + dx, y + dy), ((x + dx) * step, (y + dy) * step))
                      for dx, dy in itertools.product((-1, 0, 1), repeat=2)
                      if (dx or dy) and xmin <= x + dx <= xmax and ymin <= y + dy <= ymax]
        if np.linalg.norm(np.asarray(current.point) - p.goal) <= step * 1.5:
            neighbours.append((None, p.goal))
        for node, point in neighbours:
            candidates = [index]
            if theta and current.parent is not None:
                candidates.append(current.parent)
            best = None
            for parent_index in candidates:
                parent = labels[parent_index]
                edge = surface.edge(parent.point, point, parent.distance)
                if edge is None:
                    continue
                label = Label(node, point, parent.distance + edge.distance,
                              parent.cost + edge.cost, parent.mask | edge.mask, parent_index)
                if best is None or (metric(label), label.distance) < (metric(best), best.distance):
                    best = label
            if best is not None:
                labels.append(best)
                push(len(labels) - 1)
    return incumbent


def _rrtstar(surface, deadline, initial, safest=False):
    """RRT* with near-parent selection, rewiring, and propagated descendant costs.

    The grid solution is a feasible warm start. Altitude remains fixed because
    the contract has no climb rate, bank angle, or minimum turn radius.
    """
    if initial is None or len(initial.points) < 2:
        return initial
    if not safest and abs(initial.cost - initial.distance * surface.min_factor) < 1e-8:
        return initial  # Straight line already achieves the admissible bound.
    if safest:
        mandatory = 0
        for i, threat in surface.sources:
            if any(threat.geometry.covers(Point(point))
                   for point in ((0.0, 0.0), surface.p.goal)):
                mandatory |= 1 << i
        if initial.mask == mandatory and len(initial.points) == 2:
            return initial
    p = surface.p
    rng = np.random.default_rng(p.seed + surface.altitude)
    points = list(initial.points)
    parents = [None] + list(range(len(points) - 1))
    distances, costs, masks = [0.0], [0.0], [0]
    for a, b in itertools.pairwise(points):
        edge = surface.edge(a, b, distances[-1])
        distances.append(distances[-1] + edge.distance)
        costs.append(costs[-1] + edge.cost)
        masks.append(masks[-1] | edge.mask)
    goal_index = len(points) - 1
    lo = np.minimum.reduce(np.asarray(points)) - 30
    hi = np.maximum.reduce(np.asarray(points)) + 30
    steering = max(5.0, initial.distance / 25)

    def metric(cost, distance, mask):
        return (surface.loss(mask), distance) if safest else (cost, distance)

    def candidate(parent, point):
        edge = surface.edge(points[parent], point, distances[parent])
        if edge is None:
            return None
        return distances[parent] + edge.distance, costs[parent] + edge.cost, masks[parent] | edge.mask

    def path(index):
        result = []
        while index is not None:
            result.append(points[index])
            index = parents[index]
        return list(reversed(result))

    best = initial
    for iteration in range(250):
        if time.monotonic() >= deadline:
            break
        target = np.asarray(p.goal) if iteration % 5 == 0 else rng.uniform(lo, hi)
        array = np.asarray(points)
        nearest = int(np.argmin(np.sum((array - target) ** 2, axis=1)))
        delta = target - array[nearest]
        norm = float(np.linalg.norm(delta))
        if norm < 1e-9:
            continue
        new = tuple(array[nearest] + delta * min(1.0, steering / norm))
        radius = max(steering, 100 * math.sqrt(math.log(len(points) + 1) / len(points)))
        near = np.flatnonzero(np.sum((array - new) ** 2, axis=1) <= radius ** 2)
        candidates = [(i, candidate(i, new)) for i in near]
        candidates = [(int(i), values) for i, values in candidates if values is not None]
        if not candidates:
            continue
        parent, values = min(candidates, key=lambda row: metric(row[1][1], row[1][0], row[1][2]))
        new_index = len(points)
        points.append(new)
        parents.append(parent)
        distances.append(values[0])
        costs.append(values[1])
        masks.append(values[2])
        ancestors = set()
        ancestor = parent
        while ancestor is not None:
            ancestors.add(ancestor)
            ancestor = parents[ancestor]
        for i in near:
            i = int(i)
            if i == 0 or i in ancestors:
                continue
            replacement = candidate(new_index, points[i])
            if replacement is None or metric(replacement[1], replacement[0], replacement[2]) >= metric(costs[i], distances[i], masks[i]):
                continue
            # Re-evaluate descendants at new ETAs before accepting a rewire.
            old = parents[:], distances[:], costs[:], masks[:]
            parents[i] = new_index
            pending = [i]
            valid = True
            while pending:
                child = pending.pop()
                updated = candidate(parents[child], points[child])
                if updated is None:
                    valid = False
                    break
                distances[child], costs[child], masks[child] = updated
                pending.extend(j for j, father in enumerate(parents) if father == child)
            if not valid:
                parents[:], distances[:], costs[:], masks[:] = old
        route = assess(surface, path(goal_index))
        if route is not None and metric(route.cost, route.distance, route.mask) < metric(best.cost, best.distance, best.mask):
            best = route
    return best


def search(surface, deadline, algorithm, safest=False):
    grid_deadline = (time.monotonic() + (deadline - time.monotonic()) * .6
                     if algorithm == "rrtstar" else deadline)
    grid = _grid_search(surface, grid_deadline, theta=algorithm != "astar", safest=safest)
    if algorithm == "rrtstar":
        return _rrtstar(surface, deadline, grid, safest)
    return grid


def solve(problem, options=None) -> Solution:
    """Pure synchronous solve. All candidates share an overall time budget."""
    from garuda.optimization.plan_route.schemas import RouteWeights

    seconds = min(SEARCH_SECONDS, getattr(options, "time_limit_s", SEARCH_SECONDS))
    deadline = time.monotonic() + seconds
    algorithm = problem.payload.algorithm
    candidates = []
    for offset, altitude in enumerate(problem.altitudes):
        surface = Surface(problem, altitude, problem.payload.weights)
        # Reserve time for other altitudes, risk fallback, and alternatives.
        end = min(deadline, time.monotonic() + max(.05, (deadline - time.monotonic()) / (len(problem.altitudes) - offset + 3)))
        route = search(surface, end, algorithm)
        if route is not None:
            candidates.append(route)
    exceeded = not candidates
    if exceeded:
        for offset, altitude in enumerate(problem.altitudes):
            surface = Surface(problem, altitude, problem.payload.weights, enforce_risk=False)
            end = min(deadline, time.monotonic() + max(.05, (deadline - time.monotonic()) / (len(problem.altitudes) - offset + 1)))
            route = search(surface, end, algorithm, safest=True)
            if route is not None:
                candidates.append(route)
        if not candidates:
            raise ToolInputError("No route found within aircraft range, terrain clearance, airspace and weather coverage; search is bounded")
        route = min(candidates, key=lambda r: (Surface(problem, r.altitude, problem.payload.weights, False).loss(r.mask), r.distance, r.altitude))
    else:
        route = min(candidates, key=lambda r: (r.cost, r.distance, r.altitude))
    exceeded = route.peak_risk > 0 and route.peak_risk >= problem.payload.max_risk
    alternatives = []
    for label, weights, safest in (("shortest", RouteWeights(threat=0, weather=0, civil=0, terrain=0), False),
                                    ("safest", problem.payload.weights, True)):
        if label == "safest" and exceeded:
            # The fallback has already searched this exact risk objective.
            alternatives.append((label, route))
            continue
        surface = Surface(problem, route.altitude, weights, enforce_risk=not exceeded)
        end = min(deadline, time.monotonic() + max(.05, (deadline - time.monotonic()) / (2 if label == "shortest" else 1)))
        candidate = search(surface, end, algorithm, safest=safest)
        if candidate is not None:
            alternatives.append((label, candidate))
    if exceeded:
        alternatives.append(("least-risk (risk cap exceeded)", route))
    # Validate the actual emitted, densely sampled path independently of parents.
    surface = Surface(problem, route.altitude, problem.payload.weights, enforce_risk=not exceeded)
    dense = densify(route.points)
    validated = assess(surface, dense)
    if validated is None:
        raise ToolInputError("Route failed final segment validation; no unsafe route was returned")
    return Solution(validated, tuple(alternatives), exceeded)


def densify(points):
    result = [points[0]]
    for a, b in itertools.pairwise(points):
        count = max(1, math.ceil(math.dist(a, b)))
        result.extend(tuple(np.asarray(a) + (np.asarray(b) - a) * i / count) for i in range(1, count + 1))
    return result
