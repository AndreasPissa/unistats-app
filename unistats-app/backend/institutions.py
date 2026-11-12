"""Website destinations are separate from the RDF identifiers used in queries."""
import json
from urllib.parse import urlsplit

from .config import ROOT


def load_institutions():
    catalogue = json.loads((ROOT / "data/website-links.json").read_text(encoding="utf-8"))
    institutions = {}
    for item in catalogue["institutions"]:
        url = urlsplit(item["url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("Invalid institution website URL in website-links.json")
        institutions[item["uri"]] = {key: item[key] for key in ("label", "kind", "url")}
    return institutions
