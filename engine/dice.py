"""Seeded dice roller. Every die rolled in the engine must go through Dice.die()
so a session with the same seed and the same actions replays exactly.

Roll results are returned as dicts that match the `roll` object in
action_event.schema.json: {expr, dice, kept?, mode?, modifier, total}.
"""
import random
import re

_TERM = re.compile(r"([+-]?)\s*(\d*d\d+|\d+)")


class Dice:
    def __init__(self, seed=None):
        self.seed = seed if seed is not None else random.randrange(2**31)
        self.rng = random.Random(self.seed)
        self.count = 0  # how many dice have been rolled so far

    def die(self, sides):
        self.count += 1
        return self.rng.randint(1, sides)

    def roll(self, expr, bonus=0):
        """Roll an expression like '2d6+3', '1d8', '4d6', '2d8+1d4+2'.
        `bonus` adds a flat modifier (e.g. Rage +2) and is shown in expr."""
        expr = expr.replace(" ", "")
        dice, modifier, total = [], 0, 0
        for sign, term in _TERM.findall(expr):
            mult = -1 if sign == "-" else 1
            if "d" in term:
                n, sides = term.split("d")
                for _ in range(int(n or 1)):
                    r = self.die(int(sides))
                    dice.append(r)
                    total += mult * r
            else:
                modifier += mult * int(term)
        modifier += bonus
        total += modifier
        if bonus:
            expr = f"{expr}{bonus:+d}"
        return {"expr": expr, "dice": dice, "modifier": modifier, "total": total}

    def d20(self, modifier=0, mode="normal", extra_dice=None):
        """d20 test. mode is 'normal' | 'advantage' | 'disadvantage'.
        extra_dice: e.g. '1d4' for Bless, added to the total and listed in dice/kept.
        Returns (roll_dict, natural_d20)."""
        first = self.die(20)
        rolls = [first]
        natural = first
        if mode in ("advantage", "disadvantage"):
            second = self.die(20)
            rolls.append(second)
            natural = max(first, second) if mode == "advantage" else min(first, second)
        kept = [natural]
        total = natural + modifier
        expr = "1d20" + (f"{modifier:+d}" if modifier else "")
        if extra_dice:
            extra = self.roll(extra_dice)
            rolls += extra["dice"]
            kept += extra["dice"]
            total += extra["total"]
            expr += "+" + extra_dice
        roll = {"expr": expr, "dice": rolls, "kept": kept, "mode": mode,
                "modifier": modifier, "total": total}
        return roll, natural


def combine_mode(advantage_sources, disadvantage_sources):
    """Any advantage plus any disadvantage cancels to a normal roll."""
    adv, dis = bool(advantage_sources), bool(disadvantage_sources)
    if adv and not dis:
        return "advantage"
    if dis and not adv:
        return "disadvantage"
    return "normal"
