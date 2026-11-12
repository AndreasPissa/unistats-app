"""Load the workbook coordinate snapshot; no runtime Excel or geocoding dependency."""
import json
import math
from .config import ROOT


def load_locations():
    data = json.loads((ROOT / "data/campus-locations.json").read_text(encoding="utf-8"))
    for location in data["locations"].values():
        lat, lon = location["latitude"], location["longitude"]
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Invalid campus coordinates in the location snapshot.")
    return data
