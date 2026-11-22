"""Mechanics for the spells the pregens actually use in the demo.

spells.json only has prose descriptions, so the numbers live here.
To add a spell, add an entry; `kind` picks how rules.cast() resolves it:
  save_damage   target saves; on fail takes damage (on_success: 'none' or 'half')
  attack_damage spell attack roll vs AC, then damage
  heal          heal dice + spellcasting modifier
  buff          adds an effect to each target (Bless, Shield of Faith)
  mark          Hunter's Mark: extra damage on the caster's weapon hits vs target
"""

SPELLS = {
    "sacred_flame": {"name": "Sacred Flame", "level": 0, "kind": "save_damage",
                     "save": "dex", "on_success": "none",
                     "damage": "1d8", "damage_type": "radiant", "max_targets": 1},
    "guiding_bolt": {"name": "Guiding Bolt", "level": 1, "kind": "attack_damage",
                     "damage": "4d6", "upcast": "1d6", "damage_type": "radiant",
                     "max_targets": 1, "rider": "Guiding Bolt"},
    "cure_wounds": {"name": "Cure Wounds", "level": 1, "kind": "heal",
                    "heal": "2d8", "upcast": "2d8", "max_targets": 1},
    "bless": {"name": "Bless", "level": 1, "kind": "buff", "concentration": True,
              "effect": "Bless", "rounds": 10, "max_targets": 3},
    "shield_of_faith": {"name": "Shield of Faith", "level": 1, "kind": "buff",
                        "concentration": True, "effect": "Shield of Faith",
                        "rounds": 100, "max_targets": 1},
    "hunters_mark": {"name": "Hunter's Mark", "level": 1, "kind": "mark",
                     "concentration": True, "effect": "Hunter's Mark",
                     "rounds": 600, "damage": "1d6", "damage_type": "force",
                     "max_targets": 1},
}
