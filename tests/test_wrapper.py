"""Reject malformed wrapper checksums even when a cached distribution hides them."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WrapperIntegrityTest(unittest.TestCase):
    def test_distribution_sha256_is_exactly_64_hex_characters(self):
        properties = (ROOT / "gradle/wrapper/gradle-wrapper.properties").read_text()
        values = [line.partition("=")[2] for line in properties.splitlines()
                  if line.startswith("distributionSha256Sum=")]
        self.assertEqual(len(values), 1, "Wrapper must declare exactly one distribution checksum")
        self.assertIsNotNone(re.fullmatch(r"[0-9a-fA-F]{64}", values[0]),
                             "distributionSha256Sum must be a complete 64-character SHA-256 hex digest")


if __name__ == "__main__":
    unittest.main()
