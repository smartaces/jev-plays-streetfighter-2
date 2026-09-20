"""One versioned fighting reference shared by requests and run manifests."""
from functools import cache
from .config import ROOT

GUIDE_PATH = ROOT / "config/ryu-fighting-guide.md"
GUIDE_VERSION = "ryu-attack-opportunities-v13"


@cache
def fighting_guide():
    text = GUIDE_PATH.read_text().strip()
    if not text or len(text) > 12000:
        raise ValueError("The Ryu fighting guide is empty or too large.")
    return text
