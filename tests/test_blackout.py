"""Offline tests for Blackout (IODA, OONI, Submarine Cable Corridors, Doctrine Registers, Contract v2 Feed)."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "doctrine" / "kit" / "python"))

from blackout import cables, feed, ioda, ooni  # noqa: E402
from tic_doctrine import contract, registers  # noqa: E402

_spec = importlib.util.spec_from_file_location("blackout_cli", ROOT / "tools" / "blackout.py")
blackout_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(blackout_cli)


class TestBlackout(unittest.TestCase):
    def test_doctrine_registers_pass(self):
        self.assertEqual(registers.check(ROOT / "doctrine"), [])

    def test_ioda_and_cable_corridor_correlation(self):
        snap = blackout_cli.build_snapshot()
        self.assertEqual(len(snap["outages"]), 4)
        self.assertEqual(len(snap["corridor_alerts"]), 1)
        ca = snap["corridor_alerts"][0]
        self.assertEqual(ca["corridor_id"], "red-sea-bab-el-mandeb")
        self.assertEqual(set(ca["countries"]), {"YE", "DJ", "SD"})

    def test_feed_contract_v2_passes_linter(self):
        snap = blackout_cli.build_snapshot()
        fc = feed.build_feed(snap)
        regs = registers.ids(ROOT / "doctrine")
        self.assertGreaterEqual(len(fc["features"]), 6)
        for feat in fc["features"]:
            errs = [m for _, sev, m in contract.check(feat, regs) if sev == "error"]
            self.assertEqual(errs, [], f"Linter errors on {feat['properties'].get('id')}: {errs}")

    def test_site_build_and_check_all(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(ROOT / "doctrine", tmp / "doctrine")
            shutil.copytree(ROOT / "site_src", tmp / "site_src")
            files = blackout_cli.build_site(tmp)
            self.assertIn("data/feed.geojson", files)
            self.assertIn("index.html", files)
            self.assertEqual(blackout_cli.check_all(tmp), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
