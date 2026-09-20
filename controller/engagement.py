"""Advisory reference-point bands; never hitboxes or execution permissions.

Local moving-Guile probes found a medium punch damaging from distance 58,
low kicks from 49, and travelling/jump attacks from roughly 80–140. These
bands organise possible attacks; they do not predict contact for every move.
"""
import math

ENGAGEMENT_VERSION = "attack-distance-bands-v1"


def range_band(distance):
    if not isinstance(distance, (int, float)) or isinstance(distance, bool) or not math.isfinite(distance) or distance < 0:
        return "unknown"
    return "close" if distance <= 35 else "poke" if distance <= 65 else "entry" if distance <= 140 else "far"


def engagement(distance, projected=None):
    return {"range_band": range_band(distance),
            "distance_after_reply_estimate": projected,
            "basis": "rough_distance_bands_not_hitboxes"}
