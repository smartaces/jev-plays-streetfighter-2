"""USA character IDs; matchup advice is guidance, not an observed move."""

CHARACTERS = {0: "Ryu", 1: "E. Honda", 2: "Blanka", 3: "Guile", 4: "Ken",
              5: "Chun-Li", 6: "Zangief", 7: "Dhalsim", 8: "M. Bison",
              9: "Sagat", 10: "Balrog", 11: "Vega"}

PROFILE_VERSION = "champion-opponents-v2"
PROJECTILES = {"Ryu": "fireball", "Ken": "fireball", "Guile": "Sonic Boom",
               "Dhalsim": "Yoga Fire", "Sagat": "high/low Tiger Shot"}


def opponent_profile(name, ruleset):
    known = name in CHARACTERS.values() and ruleset in {"champion", "hyper"}
    projectile = "Kikoken" if name == "Chun-Li" and ruleset == "hyper" else PROJECTILES.get(name)
    return {"character": name, "ruleset": ruleset, "has_projectile": bool(projectile) if known else None,
            "projectile": projectile if known else None,
            "preferred_spacing": "outside_grabs" if name == "Zangief" else
                "outside_active_specials" if name in {"E. Honda", "Blanka"} else "flexible_attack_range",
            "tip": MATCHUP_TIPS.get(name, "Unknown opponent; use observed threats and the general guide.")}

# Only the current opponent's short tip is sent with each observation.
MATCHUP_TIPS = {
    "Guile": "Respect Sonic Booms and contest space with fireballs or low pokes. A hurricane kick or jump attack can exploit an unthreatened gap. Guile can intercept predictable jumps; reaching touching distance is not required to attack.",
    "Ryu": "Expect fireballs and anti-air uppercuts. Advance between projectiles; punish missed uppercuts instead of jumping predictably.",
    "Ken": "Ken can intercept predictable jumps with uppercuts. Contest ground with low pokes and fireballs; use a jump or travelling attack when an opening supports it. His identity alone is not evidence of an incoming uppercut.",
    "Zangief": "Keep outside grabbing distance. Use fireballs and intercept jumps; avoid jumping or hurricane-kicking into his arms.",
    "E. Honda": "Use space and fireballs; do not walk into rapid slaps. Guard a headbutt before trying a nearby response.",
    "Blanka": "Do not walk into electricity. Use ranged pressure; guard rolling attacks, then reassess distance.",
    "Chun-Li": "Watch jumping approaches. Stand-block or anti-air them; avoid walking into repeated kicks.",
    "Dhalsim": "Close space in guarded steps past long limbs and fireballs; use close pressure once you reach him.",
    "Balrog": "Expect fast grounded rushes. Guard first, then attack if he remains within reach.",
    "Vega": "Watch airborne approaches and side changes. Guard facing his current side; attack an exposed landing.",
    "Sagat": "Expect high/low projectiles and uppercuts. Use down-away guard; approach between shots and avoid predictable forward jumps.",
    "M. Bison": "Watch aerial approaches and rushes that cross sides. Defend facing his current position; counter only after an opening appears.",
}


def character_name(value):
    return CHARACTERS.get(value, "unknown")
