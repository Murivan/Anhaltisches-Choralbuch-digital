import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from scripts.reset_mei_measure_numbers import main, reset_measures
from scripts.split_mei_movements import PieceStart


SOURCE = """<?xml version="1.0" encoding="UTF-8"?>
<mei xmlns="http://www.music-encoding.org/ns/mei">
  <music><body>
    <mdiv xml:id="mdiv_original" n="1" label="">
      <score><section>
        <pb n="1"/>
        <measure xml:id="m1" n="1" label="1"/>
        <sb/>
        <measure xml:id="m2" n="2" label="2"/>
        <sb/>
        <measure xml:id="m3" n="3" label="3"/>
        <pb n="2"/>
        <measure xml:id="m4" n="1" label="1"/>
        <sb/>
        <measure xml:id="m5" n="2" label="2"/>
      </section></score>
    </mdiv>
  </body></music>
</mei>
"""

STARTS = {
    ("1", 1): PieceStart("First", 1),
    ("1", 3): PieceStart("Second", 0),
    ("2", 2): PieceStart("Third", 1),
}


class ResetMeasureNumbersTests(unittest.TestCase):
    def test_resets_both_attributes_and_continues_across_pages(self) -> None:
        result = reset_measures(SOURCE, STARTS, ["1", "2"])
        root = ET.fromstring(result)
        measures = root.findall(".//{http://www.music-encoding.org/ns/mei}measure")
        self.assertEqual(
            [(m.get("n"), m.get("label")) for m in measures],
            [("0", "0"), ("1", "1"), ("1", "1"), ("2", "2"), ("0", "0")],
        )
        expected = SOURCE.replace('xml:id="m1" n="1" label="1"', 'xml:id="m1" n="0" label="0"')
        expected = expected.replace('xml:id="m2" n="2" label="2"', 'xml:id="m2" n="1" label="1"')
        expected = expected.replace('xml:id="m3" n="3" label="3"', 'xml:id="m3" n="1" label="1"')
        expected = expected.replace('xml:id="m4" n="1" label="1"', 'xml:id="m4" n="2" label="2"')
        expected = expected.replace('xml:id="m5" n="2" label="2"', 'xml:id="m5" n="0" label="0"')
        self.assertEqual(result, expected)
        self.assertEqual([m.get("{http://www.w3.org/XML/1998/namespace}id") for m in measures],
                         ["m1", "m2", "m3", "m4", "m5"])

    def test_unmatched_start_and_unmapped_measure_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "CSV piece starts had no measure"):
            reset_measures(SOURCE, {**STARTS, ("2", 3): PieceStart("Missing", 0)}, ["1", "2"])
        with self.assertRaisesRegex(ValueError, "without a mapped printed page"):
            reset_measures(SOURCE, STARTS, ["1"])

    def test_cli_writes_copy_without_changing_original(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.xml"
            source.write_bytes(b"\xef\xbb\xbf" + SOURCE.encode())
            music = root / "music.csv"
            music.write_text("Title,page,accolades,anacrusis\nFirst,1,1,1\nSecond,1,3,0\nThird,2,2,1\n", encoding="utf-8")
            pages = root / "pages.csv"
            pages.write_text("filename,number\na.jpg,1\nb.jpg,2\n", encoding="utf-8")
            original = source.read_bytes()
            self.assertEqual(main([str(source), str(music), str(pages)]), 0)
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue((root / "input_copy.xml").read_bytes().startswith(b"\xef\xbb\xbf"))
            with redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    main([str(source), str(music), str(pages)])
                with self.assertRaises(SystemExit):
                    main([str(source), str(music), str(pages), "-o", str(source), "--force"])
            self.assertEqual(source.read_bytes(), original)

    def test_conflicting_csv_piece_starts_fail_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.xml"
            source.write_text(SOURCE, encoding="utf-8")
            music = root / "music.csv"
            music.write_text("Title,page,accolades,anacrusis\nFirst,1,1,1\nOther,1,1,0\n", encoding="utf-8")
            pages = root / "pages.csv"
            pages.write_text("filename,number\na.jpg,1\n", encoding="utf-8")
            with redirect_stderr(StringIO()) as errors:
                self.assertEqual(main([str(source), str(music), str(pages)]), 2)
            self.assertIn("Duplicate page/accolade start", errors.getvalue())
            self.assertFalse((root / "input_copy.xml").exists())


if __name__ == "__main__":
    unittest.main()
