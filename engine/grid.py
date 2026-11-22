"""Grid geometry for the combat map (Leena's part).

Everything here is pure functions over the `map` object in game_state.schema.json
plus the list of combatants. No dice, no state changes.

Conventions (SRD 5.2.1, 2024 rules):
  * One cell = 5 ft. Diagonal moves cost 5 ft like orthogonal ones, so the
    distance between two cells is max(dx, dy) * 5.
  * Creature size sets how many cells it fills: Tiny/Small/Medium 1x1,
    Large 2x2, Huge 3x3, Gargantuan 4x4. `position` is the top-left cell, and a
    creature's `size` field (set when the game is built) says how big it is.
    Distance between two creatures is measured between their closest cells.
  * Difficult terrain costs 5 extra feet per cell (10 ft to enter).
  * Terrain legend defaults: '.' floor, '#' wall, '~' difficult, 'D' door, 'W' water.
    Water counts as difficult terrain unless the creature has a swim speed.
  * Walls and closed or locked doors block movement and line of sight.
  * You can move through an ally's cell but not stop there. You can't move
    through a living hostile creature's cell. Dead creatures don't block.
  * Squeezing diagonally between two blocking cells is not allowed.
  * Cover (DMG grid method): from the best corner of the attacker's square,
    draw lines to the four corners of the target's square. 1-2 blocked lines =
    half cover (+2 AC and Dex saves), 3 = three-quarters (+5), 4 = total cover
    (can't be targeted). A creature or a `cover` map feature in the way gives
    half cover.

Prototype simplification: line of sight ignores light. Light is handled when
enemies try to spot the party (combat.py).
"""
import heapq
import math
import re

CELL_FT = 5
DEFAULT_LEGEND = {".": "floor", "#": "wall", "~": "difficult", "D": "door", "W": "water"}
BLOCKING_TERRAIN = {"wall"}
DIFFICULT_TERRAIN = {"difficult", "water"}
SIZE_CELLS = {"tiny": 1, "small": 1, "medium": 1, "large": 2, "huge": 3, "gargantuan": 4}
INF = float("inf")

DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


# ------------------------------------------------------------------ basics
def is_creature(p):
    return isinstance(p, dict) and "position" in p


def pos(p):
    """Accepts {'x':..,'y':..}, (x, y) or a creature; returns (x, y) (top-left for creatures)."""
    if is_creature(p):
        p = p["position"]
    if isinstance(p, dict):
        return (p["x"], p["y"])
    return (int(p[0]), int(p[1]))


def as_dict(p):
    x, y = pos(p)
    return {"x": x, "y": y}


def size_cells(c):
    """1 for Medium and smaller, 2 for Large, 3 for Huge, 4 for Gargantuan."""
    if not is_creature(c):
        return 1
    return SIZE_CELLS.get(str(c.get("size", "medium")).lower(), 1)


def footprint(c, at=None):
    """Cells a creature fills, standing at `at` (its top-left) or where it is now.
    A plain position is a single cell."""
    n = size_cells(c)
    x, y = pos(at if at is not None else c)
    return [(x + dx, y + dy) for dy in range(n) for dx in range(n)]


def at(c, p):
    """The same creature, pretending it stands at p. Used to test moves."""
    return dict(c, position=as_dict(p))


def _cells(a):
    return footprint(a) if is_creature(a) else [pos(a)]


def distance_ft(a, b):
    """2024 grid rule: diagonals cost the same as straight moves. a and b are
    cells or creatures; for creatures the closest cells count."""
    best = INF
    for ax, ay in _cells(a):
        for bx, by in _cells(b):
            best = min(best, max(abs(ax - bx), abs(ay - by)) * CELL_FT)
    return best


def adjacent(a, b):
    if set(_cells(a)) & set(_cells(b)):
        return False
    return distance_ft(a, b) <= CELL_FT


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
    """Read-only view of state['map'] plus who is standing where.
    open_doors=True treats closed (not locked) doors as passable, for AI routes
    that plan to open a door on the way."""

    def __init__(self, map_, combatants=(), open_doors=False):
        self.map = map_
        self.w, self.h = map_["width"], map_["height"]
        self.legend = {**DEFAULT_LEGEND, **map_.get("legend", {})}
        self.rows = map_["terrain"]
        self.doors = {pos(f["position"]): f for f in map_.get("features", []) if f["kind"] == "door"}
        self.cover_features = {pos(f["position"]) for f in map_.get("features", [])
                               if f["kind"] == "cover" and f.get("state") != "hidden"}
        self.combatants = [c for c in combatants if c.get("alive", True)]
        self.open_doors = open_doors
        self._occ = {}
        for c in self.combatants:
            for cell in footprint(c):
                self._occ[cell] = c

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
        door = self.doors.get(pos(p))
        if door is not None:
            return door.get("state", "closed") in ("closed", "locked")
        # A 'D' with no matching feature entry is treated as an open doorway.
        return False

    def door_locked(self, p):
        door = self.doors.get(pos(p))
        return door is not None and door.get("state") == "locked"

    def blocks_movement(self, p):
        if self.terrain(p) in BLOCKING_TERRAIN:
            return True
        if self.open_doors:
            return self.door_locked(p)
        return self.door_closed(p)

    def blocks_sight(self, p):
        return self.terrain(p) in BLOCKING_TERRAIN or self.door_closed(p)

    def area_effects(self, p):
        """Spell/hazard areas covering cell p (from map.areas)."""
        return [a for a in self.map.get("areas", []) if pos(p) in area_cells(a, self)]

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
        return self._occ.get(pos(p))

    def free(self, p, ignore_id=None):
        """Cell is walkable and nobody living is standing in it."""
        if not self.in_bounds(p) or self.blocks_movement(p):
            return False
        occ = self.occupant(p)
        return occ is None or occ["id"] == ignore_id

    def fits(self, mover, p):
        """mover could stand with its top-left at p: every cell walkable and empty."""
        for cell in footprint(mover, p):
            if not self.in_bounds(cell) or self.blocks_movement(cell):
                return False
            occ = self.occupant(cell)
            if occ is not None and occ["id"] != mover["id"]:
                return False
        return True

    # ---- line of sight
    def _clear(self, a, b):
        if a == b:
            return True
        for cell in line_cells(a, b)[1:-1]:
            if self.blocks_sight(cell):
                return False
        return True

    def line_of_sight(self, a, b):
        """Can a see b? True if at least one corner-to-corner line is clear (the
        same test as cover, so 'can see' and 'not behind total cover' always agree). Walls and closed doors block;
        the start and end cells never do (a creature in a doorway can be seen).
        For a Large creature any of its cells will do."""
        return self._wall_lines_blocked(a, b) < 4

    # ---- cover
    def _wall_lines_blocked(self, attacker, target):
        """DMG grid method: from the attacker's best corner, how many of the four
        lines to the target's corners pass through a wall or closed door (0-4).
        A line that only runs along an edge or touches a corner isn't blocked."""
        a_cells, t_cells = _cells(attacker), _cells(target)
        skip = set(a_cells) | set(t_cells)
        best = 4
        for ax, ay in a_cells:
            for corner in ((ax, ay), (ax + 1, ay), (ax, ay + 1), (ax + 1, ay + 1)):
                for tx, ty in t_cells:
                    blocked = sum(self._segment_blocked(corner, k, skip)
                                  for k in ((tx, ty), (tx + 1, ty), (tx, ty + 1), (tx + 1, ty + 1)))
                    best = min(best, blocked)
                    if best == 0:
                        return 0
        return best

    def cover(self, attacker, target):
        """0 (none), 2 (half), 5 (three-quarters) or None (total cover)."""
        blocked = self._wall_lines_blocked(attacker, target)
        if blocked == 4:
            return None
        walls = 0 if blocked == 0 else 2 if blocked <= 2 else 5
        # another creature or a cover object on every line between them: half cover
        a_cells, t_cells = _cells(attacker), _cells(target)
        skip = set(a_cells) | set(t_cells)
        others = {cell for cell, c in self._occ.items()
                  if c["id"] not in (_cid(attacker), _cid(target))} | self.cover_features
        others -= skip
        soft = 0
        if others:
            lines = [line_cells(ca, cb) for ca in a_cells for cb in t_cells]
            if all(any(cell in others for cell in ln[1:-1]) for ln in lines):
                soft = 2
        return max(walls, soft)

    def _segment_blocked(self, p, q, skip):
        """Does the segment p->q (grid-corner coordinates) pass through a wall or
        closed door? Running along the edge of one wall square or touching its
        corner doesn't count (grazing); running along the seam between two wall
        squares, or squeezing through the corner where two walls meet, does."""
        def solid(cell):
            return cell not in skip and self.blocks_sight(cell)
        (px, py), (qx, qy) = p, q
        steps = max(8, int(max(abs(qx - px), abs(qy - py)) * 16))
        for i in range(1, steps):
            t = i / steps
            x, y = px + (qx - px) * t, py + (qy - py) * t
            on_x, on_y = abs(x - round(x)) < 1e-9, abs(y - round(y)) < 1e-9
            if on_x and on_y:
                kx, ky = round(x), round(y)
                nw, ne, sw, se = (solid((kx - 1, ky - 1)), solid((kx, ky - 1)),
                                  solid((kx - 1, ky)), solid((kx, ky)))
                if (nw and se) or (ne and sw) or nw + ne + sw + se >= 3:
                    return True
            elif on_x:
                kx, cy = round(x), math.floor(y)
                if solid((kx - 1, cy)) and solid((kx, cy)):
                    return True
            elif on_y:
                cx, ky = math.floor(x), round(y)
                if solid((cx, ky - 1)) and solid((cx, ky)):
                    return True
            elif solid((math.floor(x), math.floor(y))):
                return True
        return False


def _cid(c):
    return c["id"] if is_creature(c) else None


def line_cells(a, b):
    """Cells a straight line between cell centers passes through, in order.
    Sampled at 1/16 of a cell; a line passing exactly through a corner goes
    between the two diagonal cells (it doesn't touch them)."""
    (ax, ay), (bx, by) = pos(a), pos(b)
    steps = max(abs(bx - ax), abs(by - ay)) * 16
    if steps == 0:
        return [(ax, ay)]
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
    """Dijkstra over the mover's top-left cell. Returns (cost, came_from) dicts.
    Allies can be passed through but not ended on; hostiles block. A Large
    mover needs its whole 2x2 footprint clear of walls and hostiles.
    crawling=True doubles every cost (moving while prone)."""
    start = pos(mover)
    n = size_cells(mover)
    hostile = _hostile_test(mover)
    cost = {start: 0}
    came = {start: None}
    heap = [(0, start)]
    while heap:
        c, cell = heapq.heappop(heap)
        if c > cost.get(cell, INF):
            continue
        here = set(footprint(mover, cell))
        for dx, dy in DIRS:
            nxt = (cell[0] + dx, cell[1] + dy)
            cells = footprint(mover, nxt)
            if any(not grid.in_bounds(p) or grid.blocks_movement(p) for p in cells):
                continue
            if n == 1 and dx and dy and grid.blocks_movement((cell[0] + dx, cell[1])) \
                    and grid.blocks_movement((cell[0], cell[1] + dy)):
                continue  # no squeezing between two walls diagonally
            if any((o := grid.occupant(p)) is not None and o["id"] != mover["id"] and hostile(o)
                   for p in cells):
                continue
            entered = [p for p in cells if p not in here] or cells
            step = max(grid.enter_cost(p, can_swim) for p in entered) * (2 if crawling else 1)
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


def _trace(came, cell):
    path = []
    while came[cell] is not None:
        path.append(cell)
        cell = came[cell]
    return list(reversed(path))


def path_to(grid, mover, dest, budget_ft=INF, crawling=False, can_swim=False):
    """Cheapest legal path to dest. Returns (path_without_start, feet) or (None, reason_code)."""
    dest = pos(dest)
    if not grid.fits(mover, dest):
        return None, "blocked_path"
    cost, came = find_paths(grid, mover, INF, crawling, can_swim)
    if dest not in cost:
        return None, "blocked_path"
    if cost[dest] > budget_ft:
        return None, "not_enough_movement"
    return _trace(came, dest), cost[dest]


def reachable_cells(grid, mover, budget_ft, crawling=False, can_swim=False):
    """Cells the mover can end its move in, with their cost. For GUI highlights."""
    cost, _ = find_paths(grid, mover, budget_ft, crawling, can_swim)
    start = pos(mover)
    return {c: f for c, f in cost.items() if c != start and grid.fits(mover, c)}


def best_step_toward(grid, mover, goal_cells, budget_ft, crawling=False, can_swim=False):
    """For AI: the furthest point along the cheapest path to any of goal_cells
    that fits in budget_ft. Returns (path, feet, reached_goal)."""
    cost_all, came = find_paths(grid, mover, INF, crawling, can_swim)
    goals = [g for g in map(pos, goal_cells) if g in cost_all and grid.fits(mover, g)]
    if not goals:
        return [], 0, False
    goal = min(goals, key=lambda g: (cost_all[g], g))
    full = _trace(came, goal)
    # walk as far as the budget allows, but don't stop where it doesn't fit
    best = 0
    for i, cell in enumerate(full):
        if cost_all[cell] > budget_ft:
            break
        if grid.fits(mover, cell):
            best = i + 1
    path = full[:best]
    feet = cost_all[path[-1]] if path else 0
    reached = (bool(path) and path[-1] == goal) or pos(mover) == goal
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
    When a grid is given, wall cells are dropped and sphere/cone/line cells must
    have line of sight to the origin (total cover blocks area effects).
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
        cells = {c for c in cells if grid.in_bounds(c) and grid.terrain(c) not in BLOCKING_TERRAIN
                 and not grid.door_closed(c)}
        if shape != "cube":
            cells = {c for c in cells if grid._clear((ox, oy), c)}
    return cells


def creatures_in_area(area, grid, toward=None):
    """Creatures with any of their cells inside the area."""
    cells = area_cells(area, grid, toward)
    return [c for c in grid.combatants if any(p in cells for p in footprint(c))]


# ------------------------------------------------------------------ weapon ranges
_RANGE = re.compile(r"(\d+)\s*/\s*(\d+)")
_REACH = re.compile(r"reach\s*(\d+)", re.I)


def weapon_range(weapon, thrown=False):
    """Returns ('melee', reach_ft, reach_ft) or ('ranged', normal_ft, long_ft).
    Reads 'Thrown 20/60', '120/600 ft.' or 'reach 10 ft.' from the weapon's notes.
    A thrown weapon is melee unless thrown=True."""
    notes = (weapon.get("notes") or "")
    m = _RANGE.search(notes)
    is_thrown = "thrown" in notes.lower()
    if "range" in weapon and isinstance(weapon["range"], dict):
        r = weapon["range"]
        return ("ranged", r.get("normal", 30), r.get("long", r.get("normal", 30)))
    if m and (thrown or not is_thrown):
        return ("ranged", int(m.group(1)), int(m.group(2)))
    reach = weapon.get("reach_ft") or weapon.get("reach")
    if reach is None:
        r = _REACH.search(notes)
        reach = int(r.group(1)) if r else 5
    return ("melee", parse_speed(reach), parse_speed(reach))
