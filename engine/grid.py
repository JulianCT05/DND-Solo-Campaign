"""Grid geometry for the combat map (Leena's part).

Everything here is pure functions over the `map` object in game_state.schema.json
plus the list of combatants. No dice, no state changes.

Conventions (SRD 5.2.1, 2024 rules):
  * One cell = 5 ft. Diagonal moves cost 5 ft like orthogonal ones, so the
    distance between two cells is max(dx, dy) * 5.
  * Difficult terrain costs 5 extra feet per cell (10 ft to enter).
  * Terrain legend defaults: '.' floor, '#' wall, '~' difficult, 'D' door, 'W' water.
    Water counts as difficult terrain unless the creature has a swim speed.
  * Walls and closed or locked doors block movement and line of sight.
  * You can move through an ally's cell but not stop there. You can't move
    through a living hostile creature's cell. Dead creatures don't block.
  * Squeezing diagonally between two blocking cells is not allowed.

Prototype simplifications: every creature fills one cell (no Large 2x2 tokens),
line of sight is a single center-to-center line (no partial cover), and light
level doesn't change what anyone can see.
"""
import heapq
import math
import re

CELL_FT = 5
DEFAULT_LEGEND = {".": "floor", "#": "wall", "~": "difficult", "D": "door", "W": "water"}
BLOCKING_TERRAIN = {"wall"}
DIFFICULT_TERRAIN = {"difficult", "water"}
INF = float("inf")

DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


# ------------------------------------------------------------------ basics
def pos(p):
    """Accepts {'x':..,'y':..} or (x, y); returns (x, y)."""
    if isinstance(p, dict):
        return (p["x"], p["y"])
    return (int(p[0]), int(p[1]))


def as_dict(p):
    x, y = pos(p)
    return {"x": x, "y": y}


def distance_ft(a, b):
    """2024 grid rule: diagonals cost the same as straight moves."""
    (ax, ay), (bx, by) = pos(a), pos(b)
    return max(abs(ax - bx), abs(ay - by)) * CELL_FT


def adjacent(a, b):
    return pos(a) != pos(b) and distance_ft(a, b) <= CELL_FT


def parse_speed(speed):
    """30 -> 30, '30 ft., swim 30 ft.' -> 30. Walking speed only."""
    if isinstance(speed, int):
        return speed
    m = re.search(r"\d+", str(speed or ""))
    return int(m.group()) if m else 30


def has_swim_speed(speed):
    return "swim" in str(speed or "").lower()


# ------------------------------------------------------------------ the map
class Grid:
    """Read-only view of state['map'] plus who is standing where."""

    def __init__(self, map_, combatants=()):
        self.map = map_
        self.w, self.h = map_["width"], map_["height"]
        self.legend = {**DEFAULT_LEGEND, **map_.get("legend", {})}
        self.rows = map_["terrain"]
        self.doors = {pos(f["position"]): f for f in map_.get("features", []) if f["kind"] == "door"}
        self.combatants = [c for c in combatants if c.get("alive", True)]

    # ---- terrain
    def in_bounds(self, p):
        x, y = pos(p)
        return 0 <= x < self.w and 0 <= y < self.h

    def terrain(self, p):
        x, y = pos(p)
        if not self.in_bounds((x, y)):
            return "wall"
        row = self.rows[y] if y < len(self.rows) else ""
        ch = row[x] if x < len(row) else "#"
        return self.legend.get(ch, "floor")

    def door_closed(self, p):
        p = pos(p)
        door = self.doors.get(p)
        if door is not None:
            return door.get("state", "closed") in ("closed", "locked")
        # A 'D' with no matching feature entry is treated as an open doorway.
        return False

    def blocks_movement(self, p):
        return self.terrain(p) in BLOCKING_TERRAIN or self.door_closed(p)

    def blocks_sight(self, p):
        return self.terrain(p) in BLOCKING_TERRAIN or self.door_closed(p)

    def area_effects(self, p):
        """Spell/hazard areas covering cell p (from map.areas)."""
        hits = []
        for a in self.map.get("areas", []):
            if pos(p) in area_cells(a, self):
                hits.append(a)
        return hits

    def is_difficult(self, p, can_swim=False):
        t = self.terrain(p)
        if t == "water" and can_swim:
            return False
        if t in DIFFICULT_TERRAIN:
            return True
        return any("difficult" in (a.get("effect") or "") for a in self.area_effects(p))

    def enter_cost(self, p, can_swim=False):
        return CELL_FT * 2 if self.is_difficult(p, can_swim) else CELL_FT

    # ---- creatures
    def occupant(self, p):
        p = pos(p)
        for c in self.combatants:
            if pos(c["position"]) == p:
                return c
        return None

    def free(self, p, ignore_id=None):
        """Cell is walkable and nobody living is standing in it."""
        if not self.in_bounds(p) or self.blocks_movement(p):
            return False
        occ = self.occupant(p)
        return occ is None or occ["id"] == ignore_id

    # ---- line of sight
    def line_of_sight(self, a, b):
        """Center-to-center line. Blocked if it passes through a wall or a closed
        door. The start and end cells never block (a creature in a doorway can
        still be seen)."""
        (ax, ay), (bx, by) = pos(a), pos(b)
        if (ax, ay) == (bx, by):
            return True
        for cell in line_cells((ax, ay), (bx, by))[1:-1]:
            if self.blocks_sight(cell):
                return False
        return True


def line_cells(a, b):
    """Cells a straight line between cell centers passes through, in order.
    Sampled at 1/16 of a cell; a line passing exactly through a corner goes
    between the two diagonal cells (it doesn't touch them)."""
    (ax, ay), (bx, by) = pos(a), pos(b)
    steps = max(abs(bx - ax), abs(by - ay)) * 16
    cells = []
    for i in range(steps + 1):
        t = i / steps
        x = ax + 0.5 + (bx - ax) * t
        y = ay + 0.5 + (by - ay) * t
        # exactly on a grid line: nudge toward the destination so corners don't count twice
        cx = math.floor(x + (1e-9 if bx >= ax else -1e-9))
        cy = math.floor(y + (1e-9 if by >= ay else -1e-9))
        if not cells or cells[-1] != (cx, cy):
            cells.append((cx, cy))
    return cells


# ------------------------------------------------------------------ pathfinding
def find_paths(grid, mover, budget_ft=INF, crawling=False, can_swim=False):
    """Dijkstra from the mover's cell. Returns (cost, came_from) dicts.
    Allies can be passed through but not ended on; hostiles block.
    crawling=True doubles every cost (moving while prone)."""
    start = pos(mover["position"])
    hostile = _hostile_test(mover)
    cost = {start: 0}
    came = {start: None}
    heap = [(0, start)]
    while heap:
        c, cell = heapq.heappop(heap)
        if c > cost.get(cell, INF):
            continue
        for dx, dy in DIRS:
            nxt = (cell[0] + dx, cell[1] + dy)
            if not grid.in_bounds(nxt) or grid.blocks_movement(nxt):
                continue
            if dx and dy and grid.blocks_movement((cell[0] + dx, cell[1])) \
                    and grid.blocks_movement((cell[0], cell[1] + dy)):
                continue  # no squeezing between two walls diagonally
            occ = grid.occupant(nxt)
            if occ is not None and occ["id"] != mover["id"] and hostile(occ):
                continue
            step = grid.enter_cost(nxt, can_swim) * (2 if crawling else 1)
            nc = c + step
            if nc > budget_ft or nc >= cost.get(nxt, INF):
                continue
            cost[nxt] = nc
            came[nxt] = cell
            heapq.heappush(heap, (nc, nxt))
    return cost, came


def _hostile_test(mover):
    mover_is_pc = "controller" in mover
    return lambda other: ("controller" in other) != mover_is_pc


def path_to(grid, mover, dest, budget_ft=INF, crawling=False, can_swim=False):
    """Cheapest legal path to dest. Returns (path_without_start, feet) or (None, reason_code)."""
    dest = pos(dest)
    if not grid.in_bounds(dest) or grid.blocks_movement(dest):
        return None, "blocked_path"
    occ = grid.occupant(dest)
    if occ is not None and occ["id"] != mover["id"]:
        return None, "blocked_path"
    cost, came = find_paths(grid, mover, INF, crawling, can_swim)
    if dest not in cost:
        return None, "blocked_path"
    if cost[dest] > budget_ft:
        return None, "not_enough_movement"
    path, cell = [], dest
    while came[cell] is not None:
        path.append(cell)
        cell = came[cell]
    return list(reversed(path)), cost[dest]


def reachable_cells(grid, mover, budget_ft, crawling=False, can_swim=False):
    """Cells the mover can end its move in, with their cost. For GUI highlights."""
    cost, _ = find_paths(grid, mover, budget_ft, crawling, can_swim)
    start = pos(mover["position"])
    return {c: f for c, f in cost.items() if c != start and grid.occupant(c) is None}


def best_step_toward(grid, mover, goal_cells, budget_ft, crawling=False, can_swim=False):
    """For AI: the furthest point along the cheapest path to any of goal_cells
    that fits in budget_ft. Returns (path, feet, reached_goal)."""
    cost_all, came = find_paths(grid, mover, INF, crawling, can_swim)
    goals = [g for g in map(pos, goal_cells) if g in cost_all and
             (grid.occupant(g) is None or grid.occupant(g)["id"] == mover["id"])]
    if not goals:
        return [], 0, False
    goal = min(goals, key=lambda g: (cost_all[g], g))
    full, cell = [], goal
    while came[cell] is not None:
        full.append(cell)
        cell = came[cell]
    full.reverse()
    # walk as far as the budget allows, but don't stop on an occupied cell
    best = 0
    for i, cell in enumerate(full):
        if cost_all[cell] > budget_ft:
            break
        if grid.occupant(cell) is None:
            best = i + 1
    path = full[:best]
    feet = cost_all[path[-1]] if path else 0
    reached = (bool(path) and path[-1] == goal) or pos(mover["position"]) == goal
    return path, feet, reached


# ------------------------------------------------------------------ spell areas
def area_cells(area, grid=None, toward=None):
    """Cells covered by an area of effect (SRD 5.2.1 templates on a grid).

    area: {'shape', 'origin', 'size_ft'} as in map.areas. `toward` is a point
    that gives the direction for cones and lines (defaults to +x).
      sphere / cylinder : cells whose center is within size_ft (radius) of the origin point
      emanation         : every cell within size_ft of the origin creature's cell
      cube              : size_ft square with the origin at its top-left corner
      cone              : length size_ft; width at any point = distance from origin
      line              : size_ft long, 5 ft wide
    Walls stop nothing here except that sphere/cone/line cells must have line of
    sight to the origin when a grid is given (total cover blocks area effects).
    """
    shape = area["shape"]
    size = area.get("size_ft", 5)
    ox, oy = pos(area["origin"])
    n = size // CELL_FT
    cells = set()
    if shape in ("sphere", "cylinder"):
        r = size / CELL_FT
        for x in range(ox - n, ox + n + 1):
            for y in range(oy - n, oy + n + 1):
                if math.hypot(x - ox, y - oy) <= r + 1e-9:
                    cells.add((x, y))
    elif shape == "emanation":
        for x in range(ox - n, ox + n + 1):
            for y in range(oy - n, oy + n + 1):
                cells.add((x, y))
        cells.discard((ox, oy))
    elif shape == "cube":
        for x in range(ox, ox + max(1, n)):
            for y in range(oy, oy + max(1, n)):
                cells.add((x, y))
    elif shape in ("cone", "line"):
        tx, ty = pos(toward) if toward is not None else (ox + 1, oy)
        dx, dy = tx - ox, ty - oy
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        for x in range(ox - n, ox + n + 1):
            for y in range(oy - n, oy + n + 1):
                vx, vy = x - ox, y - oy
                along = vx * ux + vy * uy
                across = abs(-vx * uy + vy * ux)
                if along <= 0 or along > n + 1e-9:
                    continue
                if shape == "cone" and across <= along / 2 + 0.5:
                    cells.add((x, y))
                if shape == "line" and across <= 0.5:
                    cells.add((x, y))
    if grid is not None:
        cells = {c for c in cells if grid.in_bounds(c) and not grid.blocks_movement(c)}
        if shape != "cube":
            cells = {c for c in cells if grid.line_of_sight((ox, oy), c)}
    return cells


def creatures_in_area(area, grid, toward=None):
    cells = area_cells(area, grid, toward)
    return [c for c in grid.combatants if pos(c["position"]) in cells]


# ------------------------------------------------------------------ weapon ranges
_RANGE = re.compile(r"(\d+)\s*/\s*(\d+)")


def weapon_range(weapon, thrown=False):
    """Returns ('melee', reach_ft, reach_ft) or ('ranged', normal_ft, long_ft).
    Reads 'Thrown 20/60' or '120/600 ft.' from the weapon's notes.
    A thrown weapon is melee unless thrown=True."""
    notes = (weapon.get("notes") or "")
    m = _RANGE.search(notes)
    is_thrown = "thrown" in notes.lower()
    if "range" in weapon and isinstance(weapon["range"], dict):
        r = weapon["range"]
        return ("ranged", r.get("normal", 30), r.get("long", r.get("normal", 30)))
    if m and (thrown or not is_thrown):
        return ("ranged", int(m.group(1)), int(m.group(2)))
    reach = weapon.get("reach_ft") or weapon.get("reach") or 5
    return ("melee", parse_speed(reach), parse_speed(reach))
