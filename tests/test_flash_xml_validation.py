from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from flash_xml_validation import validate_flash_xml


class FlashXML(unittest.TestCase):
    def test_real_official_fragments_preserved(self):
        raw = '<root><item tips="幻境界皇&时空界皇，难度1&2"/></root>'
        before = raw.encode()
        self.assertEqual(validate_flash_xml(raw), 2)
        self.assertEqual(validate_flash_xml(raw), 2)
        self.assertEqual(before, raw.encode())

    def test_entities_comments_cdata_and_quotes(self):
        self.assertEqual(validate_flash_xml('<r a="a>b&amp;c&#38;&#x26;">a& b<!-- & --><![CDATA[ & ]]></r>'), 1)

    def test_invalid_inputs_still_fail(self):
        for raw in ('<r>&unknown;</r>', '<r>&#0;</r>', '<r>\x00</r>', '<r><a></r>', '<r', '<!DOCTYPE r [<!ENTITY x "ok">]><r/>', '<r><!--'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, ET.ParseError)):
                validate_flash_xml(raw)


if __name__ == '__main__':
    unittest.main()
