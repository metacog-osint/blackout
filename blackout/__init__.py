"""Blackout: internet connectivity, BGP routing, censorship, and submarine cable outage monitor."""
import sys
from pathlib import Path

__version__ = "0.2.0"

# The doctrine kit (tic_doctrine) ships inside the repo; make it importable from every module, not only the CLI.
_KIT = Path(__file__).resolve().parents[1] / "doctrine" / "kit" / "python"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
