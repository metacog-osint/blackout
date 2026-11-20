"""The satellite networks register (blackout/satcom.py): complete, closed classes, nothing excluded, searchable."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackout import satcom  # noqa: E402

FIELDS = ("asn", "operator", "registry_holder", "registry_country", "registry", "class", "service", "evidence",
          "confidence")


class TestRegister(unittest.TestCase):
    def setUp(self):
        self.doc = satcom.load()
        self.nets = self.doc["networks"]

    def test_every_network_has_its_fields_and_a_closed_class(self):
        for n in self.nets:
            for k in FIELDS:
                self.assertIn(k, n, (n.get("asn"), k))
            self.assertIsInstance(n["asn"], int)
            self.assertIn(n["class"], satcom.CLASSES, n["asn"])
            self.assertIn(n["confidence"], satcom.CONFIDENCE, n["asn"])
            self.assertTrue(n["evidence"].strip(), n["asn"])

    def test_one_row_per_asn(self):
        asns = [n["asn"] for n in self.nets]
        self.assertEqual(len(asns), len(set(asns)))

    def test_lookalikes_stay_in_and_say_what_they_are(self):
        by = {n["asn"]: n for n in self.nets}
        self.assertEqual(by[14593]["class"], "satellite_internet")
        self.assertEqual(by[34602]["class"], "not_satellite")   # Moscow home ISP named Starlink, not Rassvet
        self.assertEqual(by[29242]["class"], "satellite_tv")    # Viasat Ukraine's pay-TV company
        self.assertNotIn(16509, by)                             # Amazon's AWS network is not Kuiper

    def test_search_matches_name_or_number_and_filters_by_class(self):
        self.assertIn(34602, [n["asn"] for n in satcom.search(self.nets, "starlink")])
        self.assertNotIn(34602, [n["asn"] for n in satcom.search(self.nets, "starlink", satcom.COUNTED)])
        self.assertEqual([n["asn"] for n in satcom.search(self.nets, "AS14593")], [14593])
        self.assertEqual(len(satcom.search(self.nets)), len(self.nets))

    def test_snapshot_carries_the_whole_register(self):
        part = satcom.snapshot_part(self.doc)
        self.assertEqual(len(part["networks"]), len(self.nets))
        self.assertEqual(part["classes"], list(satcom.CLASSES))


if __name__ == "__main__":
    unittest.main()
