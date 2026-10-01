"""One-time webfont preparation; not part of the application build.

Usage: python design/fonts/subset.py /path/to/downloaded/ttfs
Requires fonttools[woff]==4.60.0, brotli==1.2.0, zopfli==0.4.3.
"""
from hashlib import sha256
from pathlib import Path
import sys

from fontTools import subset
from fontTools.ttLib import TTFont

destination = Path(__file__).resolve().parent
source = Path(sys.argv[1])
license_text = (destination / "LICENSE").read_text()
hashes = {
    "Regular": "701d7ec08f58f07251c1758361c5d1ab57ba0a867dd378cbb0fa52e1d2beccad",
    "Bold": "d38b2e9461f52c70ef9f18c5c79f869d8b084432416ea6e850855c5222fbdc38",
}
unicodes = subset.parse_unicodes(
    "0000-024F,0300-03FF,1E00-1EFF,2000-23FF,2500-25FF,27F0-27FF,2900-297F"
)
for weight, expected in hashes.items():
    path = source / f"CaskaydiaCoveNerdFont-{weight}.ttf"
    if sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Unexpected source font: {path.name}")
    font = TTFont(path)
    # Carry the complete OFL with the font when served independently of the repo.
    font["name"].setName(license_text, 13, 3, 1, 0x409)
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(font)
    font.flavor = "woff2"
    font.save(destination / f"caskaydia-cove-{weight.lower()}.woff2")
