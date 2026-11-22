"""Grid geometry: distance, pathing, terrain, doors, line of sight, areas, ranges."""
from engine import grid as G

ROOM = {"width": 8, "height": 6, "cell_ft": 5,
        "terrain": ["########",
                    "#......#",
                    "#..~~..#",
                    "#..#...#",
                    "#..#..D#",
                    "########"],
        "legend": {"D": "door", "~": "difficult"},
        "features": [{"id": "door", "kind": "door", "position": {"x": 6, "y": 4}, "state": "closed"}]}


def pc(id_, x, y):
    return {"id": id_, "position": {"x": x, "y": y}, "alive": True, "controller": "player"}


def foe(id_, x, y, alive=True):
    return {"id": id_, "position": {"x": x, "y": y}, "alive": alive, "ai": {}}


def test_distance_diagonals_cost_5ft():
    assert G.distance_ft((1, 1), (4, 3)) == 15
    assert G.distance_ft((1, 1), (2, 2)) == 5
    assert G.adjacent((1, 1), (2, 2)) and not G.adjacent((1, 1), (1, 1))


def test_parse_speed():
    assert G.parse_speed(35) == 35
    assert G.parse_speed("30 ft., swim 30 ft.") == 30
    assert G.has_swim_speed("30 ft., swim 30 ft.") and not G.has_swim_speed(30)


def test_path_around_wall_and_difficult_terrain_costs_double():
    g = G.Grid(ROOM)
    me = pc("a", 1, 2)
    path, feet = G.path_to(g, me, (5, 2))
    # straight line crosses two difficult squares; going around via row 1 is also 20 ft
    assert feet == 20 and path[-1] == (5, 2)
    path, feet = G.path_to(g, me, (3, 2))
    assert feet == 15  # 5 to (2,2) then 10 to enter difficult (3,2)


def test_walls_and_closed_doors_block():
    g = G.Grid(ROOM)
    assert G.path_to(g, pc("a", 1, 1), (0, 1)) == (None, "blocked_path")
    assert G.path_to(g, pc("a", 5, 4), (6, 4)) == (None, "blocked_path")
    ROOM2 = {**ROOM, "features": [{"id": "door", "kind": "door", "position": {"x": 6, "y": 4}, "state": "open"}]}
    path, feet = G.path_to(G.Grid(ROOM2), pc("a", 5, 4), (6, 4))
    assert path == [(6, 4)] and feet == 5


def test_budget_limit():
    g = G.Grid(ROOM)
    assert G.path_to(g, pc("a", 1, 1), (6, 1), budget_ft=20) == (None, "not_enough_movement")
    assert G.path_to(g, pc("a", 1, 1), (6, 1), budget_ft=25)[1] == 25


def test_allies_pass_hostiles_block_and_no_ending_on_anyone():
    me, ally, enemy = pc("a", 1, 1), pc("b", 2, 1), foe("e", 2, 2)
    ally["controller"] = "engine_ally"
    g = G.Grid(ROOM, [me, ally, enemy])
    path, _ = G.path_to(g, me, (3, 1))
    assert (2, 1) in path  # walked through the ally
    assert G.path_to(g, me, (2, 1)) == (None, "blocked_path")  # can't stop on the ally
    cost, _ = G.find_paths(g, me)
    assert (2, 2) not in cost  # can't enter the enemy's cell
    dead = foe("d", 2, 2, alive=False)
    g2 = G.Grid(ROOM, [me, dead])
    assert G.path_to(g2, me, (2, 2))[0] == [(2, 2)]  # corpses don't block


def test_crawling_doubles_cost():
    g = G.Grid(ROOM)
    assert G.path_to(g, pc("a", 1, 1), (3, 1), crawling=True)[1] == 20


def test_water_is_difficult_unless_you_can_swim():
    m = {**ROOM, "terrain": ["########", "#.WW...#", "#......#", "#......#", "#......#", "########"],
         "legend": {"W": "water"}, "features": []}
    g = G.Grid(m)
    walk = G.path_to(g, pc("a", 1, 1), (2, 1))[1]
    swim = G.path_to(g, pc("a", 1, 1), (2, 1), can_swim=True)[1]
    assert (walk, swim) == (10, 5)


def test_line_of_sight():
    g = G.Grid(ROOM)
    # the wall stub at (3,3)-(3,4) hides (6,3) only partly: visible over its top edge
    assert g.line_of_sight((1, 3), (6, 3)) is True
    assert g.cover((1, 3), (6, 3)) == 2
    # walls above and below the stub's row leave no line at all: total cover
    assert g.line_of_sight((1, 4), (5, 4)) is False and g.cover((1, 4), (5, 4)) is None
    assert g.line_of_sight((1, 1), (6, 1)) is True
    assert g.line_of_sight((5, 4), (6, 4)) is True    # adjacent, door is the end cell
    assert g.line_of_sight((1, 4), (2, 1)) is True
    assert g.line_of_sight((2, 4), (5, 2)) is False   # through the wall at (3,3)


def test_no_diagonal_squeeze_between_two_walls():
    m = {"width": 4, "height": 4, "cell_ft": 5,
         "terrain": ["....", ".#..", "#...", "...."], "features": []}
    g = G.Grid(m)
    cost, _ = G.find_paths(g, pc("a", 0, 1))
    # (0,1) -> (1,2) would squeeze between the walls at (1,1) and (0,2)
    assert cost[(1, 2)] > 5


def test_reachable_cells_respects_budget():
    g = G.Grid(ROOM)
    cells = G.reachable_cells(g, pc("a", 1, 1), 10)
    assert (3, 1) in cells and (4, 1) not in cells and (1, 1) not in cells


def test_area_shapes():
    sphere = G.area_cells({"shape": "sphere", "origin": {"x": 10, "y": 10}, "size_ft": 10})
    assert (10, 10) in sphere and (12, 10) in sphere and (12, 12) not in sphere and len(sphere) == 13
    cube = G.area_cells({"shape": "cube", "origin": {"x": 0, "y": 0}, "size_ft": 15})
    assert len(cube) == 9
    eman = G.area_cells({"shape": "emanation", "origin": {"x": 5, "y": 5}, "size_ft": 5})
    assert len(eman) == 8 and (5, 5) not in eman
    line = G.area_cells({"shape": "line", "origin": {"x": 0, "y": 0}, "size_ft": 30}, toward=(10, 0))
    assert line == {(x, 0) for x in range(1, 7)}
    cone = G.area_cells({"shape": "cone", "origin": {"x": 0, "y": 5}, "size_ft": 15}, toward=(5, 5))
    assert (1, 5) in cone and (3, 4) in cone and (3, 6) in cone and (1, 3) not in cone and (4, 5) not in cone


def test_area_stops_at_walls_and_makes_difficult_terrain():
    m = {**ROOM, "areas": [{"id": "web", "source": "Web", "shape": "cube",
                            "origin": {"x": 5, "y": 1}, "size_ft": 10, "effect": "difficult_terrain"}]}
    g = G.Grid(m)
    assert g.is_difficult((5, 1)) and g.is_difficult((6, 2)) and not g.is_difficult((4, 1))
    sphere = G.area_cells({"shape": "sphere", "origin": {"x": 1, "y": 3}, "size_ft": 15}, g)
    assert (4, 3) not in sphere and (3, 3) not in sphere  # wall cell and the cell behind it


def test_weapon_ranges_from_notes():
    assert G.weapon_range({"name": "Greataxe", "notes": "Cleave"}) == ("melee", 5, 5)
    assert G.weapon_range({"name": "Longbow", "notes": "120/600 ft., Slow"}) == ("ranged", 120, 600)
    jav = {"name": "Javelin", "notes": "Thrown 30/120"}
    assert G.weapon_range(jav) == ("melee", 5, 5)
    assert G.weapon_range(jav, thrown=True) == ("ranged", 30, 120)
