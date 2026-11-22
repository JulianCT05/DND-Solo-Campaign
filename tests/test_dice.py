from engine import Dice
from engine.dice import combine_mode


def test_same_seed_same_rolls():
    a, b = Dice(123), Dice(123)
    assert [a.roll("3d6+2")["total"] for _ in range(20)] == [b.roll("3d6+2")["total"] for _ in range(20)]


def test_expression_parsing():
    d = Dice(1)
    r = d.roll("2d8+1d4+3")
    assert len(r["dice"]) == 3 and r["modifier"] == 3
    assert r["total"] == sum(r["dice"]) + 3
    r = d.roll("1d12+3", bonus=2)
    assert r["modifier"] == 5 and r["expr"] == "1d12+3+2"


def test_advantage_and_disadvantage():
    d = Dice(7)
    for _ in range(50):
        r, nat = d.d20(5, "advantage")
        assert nat == max(r["dice"]) and r["total"] == nat + 5
        r, nat = d.d20(5, "disadvantage")
        assert nat == min(r["dice"])


def test_bless_die_is_listed():
    r, nat = Dice(3).d20(4, "normal", "1d4")
    assert len(r["dice"]) == 2 and r["total"] == sum(r["kept"]) + 4 and r["expr"] == "1d20+4+1d4"


def test_adv_and_dis_cancel():
    assert combine_mode(["reckless"], ["poisoned"]) == "normal"
    assert combine_mode(["a", "b"], []) == "advantage"
