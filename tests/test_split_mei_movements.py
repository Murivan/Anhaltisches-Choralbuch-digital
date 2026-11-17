import re
import unittest
import xml.etree.ElementTree as ET

from scripts.split_mei_movements import PieceStart, split_mei_text


MEI_NS = "http://www.music-encoding.org/ns/mei"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


def element(name: str) -> str:
    return f"{{{MEI_NS}}}{name}"


class SplitMeiMovementsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = """<?xml version="1.0" encoding="UTF-8"?>
<mei xmlns="http://www.music-encoding.org/ns/mei">
  <music>
    <facsimile/>
    <body>
      <mdiv xml:id="mdiv_ORIGINAL" n="1" label="">
        <score>
          <scoreDef/>
          <section>
            <pb n="1"/>
            <measure xml:id="measure_1" n="0" label="0"/>
            <sb/>
            <measure xml:id="measure_2" n="1" label="1"/>
            <measure xml:id="measure_3" n="2" label="2"/>
            <sb/>
            <measure xml:id="measure_4" n="1" label="1"/>
            <pb n="2"/>
            <measure xml:id="measure_5" n="0" label="0"/>
          </section>
        </score>
      </mdiv>
    </body>
  </music>
</mei>
"""
        self.starts = {
            ("1", 1): PieceStart("First & song", 1),
            ("1", 3): PieceStart("Second\nsong", 0),
            ("2", 1): PieceStart("Third song", 1),
        }

    def test_splits_on_resets_and_titles_each_movement(self) -> None:
        output = split_mei_text(self.source, self.starts, ["1", "2"])
        root = ET.fromstring(output)
        movements = [node for node in root.iter() if node.tag == element("mdiv")]

        self.assertEqual([start.title for start in self.starts.values()], [
            node.get("label") for node in movements
        ])
        self.assertEqual([node.get("n") for node in movements], ["1", "2", "3"])
        ids = [node.get(XML_ID) for node in movements]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"mdiv_[0-9A-HJKMNP-TV-Z]{26}", value) for value in ids))
        self.assertNotIn("mdiv_ORIGINAL", ids)

        measures = [
            [node for node in movement.iter() if node.tag == element("measure")]
            for movement in movements
        ]
        self.assertEqual([[measure.get("n") for measure in group] for group in measures],
                         [["0", "1", "2"], ["1"], ["0"]])
        self.assertEqual(len([node for node in root.iter() if node.tag == element("measure")]), 5)

        second_section = next(node for node in movements[1].iter() if node.tag == element("section"))
        self.assertEqual(
            [node.tag for node in second_section],
            [element("pb"), element("sb"), element("sb"), element("measure")],
        )
        self.assertEqual(self.source.count('xml:id="measure_'), output.count('xml:id="measure_'))
        self.assertEqual(self.source.count("<pb ") + 1, output.count("<pb "))

    def test_rejects_resets_without_matching_csv_start(self) -> None:
        wrong_starts = {
            ("1", 1): PieceStart("First song", 1),
            ("2", 1): PieceStart("Missing second piece", 1),
        }
        with self.assertRaisesRegex(ValueError, "No page_music.csv title"):
            split_mei_text(self.source, wrong_starts, ["1", "2"])


if __name__ == "__main__":
    unittest.main()
