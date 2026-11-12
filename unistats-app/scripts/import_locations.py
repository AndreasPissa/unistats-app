"""Read the supplied workbook without editing it; export a portable map snapshot.

Usage: python scripts/import_locations.py [path/to/cs_schools.xlsx]
The workbook's G column identifies departments; K/L contain LAT/LONG.
Only the standard library is required. Blank formatted spreadsheet rows are ignored.
"""
import hashlib
import json
import math
import sys
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_workbook(path):
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(node.itertext()) for node in
                       ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        locations = {}
        for row in sheet.findall(".//s:sheetData/s:row", NS):
            cells = {}
            for cell in row:
                column = "".join(c for c in cell.attrib["r"] if c.isalpha())
                value, inline = cell.find("s:v", NS), cell.find("s:is", NS)
                text = value.text if value is not None else "".join(inline.itertext()) if inline is not None else ""
                cells[column] = (strings[int(text)] if cell.attrib.get("t") == "s" else text or "").strip()
            if row.attrib["r"] == "1":
                if cells.get("K") != "LAT" or cells.get("L") != "LONG":
                    raise ValueError("Expected LAT/LONG in workbook columns K/L.")
                continue
            uri = cells.get("G", "")
            if not uri.startswith(("http://", "https://")):
                continue
            latitude, longitude = float(cells["K"]), float(cells["L"])
            if not (math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180):
                raise ValueError(f"Invalid coordinates in row {row.attrib['r']}")
            if uri in locations:
                raise ValueError(f"Duplicate department in row {row.attrib['r']}")
            locations[uri] = {"latitude": latitude, "longitude": longitude,
                              "sheet": "Sheet1", "row": int(row.attrib["r"])}
    if not locations:
        raise ValueError("No department coordinates found.")
    return {"source": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "prepared": date.today().isoformat(), "locations": locations}


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "cs_schools.xlsx"
    result = read_workbook(source)
    target = ROOT / "data/campus-locations.json"
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {len(result['locations'])} locations into {target.name}. Source workbook unchanged.")
