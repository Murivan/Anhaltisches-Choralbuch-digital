#!/usr/bin/env python3
"""Split a single-mdiv MEI annotation file into one movement per piece."""

from __future__ import annotations

import argparse
import csv
import re
import secrets
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
ID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ATTRIBUTE_RE = re.compile(r"""(?<![\w:.-])(?P<name>[\w:.-]+)\s*=\s*(?P<quote>["'])(?P<value>.*?)\2""")
EVENT_LINE_RE = re.compile(
    r"(?P<indent>[ \t]*)<(?P<name>pb|sb|measure)\b(?P<attributes>[^>]*)/\s*>[ \t]*"
)
SECTION_OPEN_RE = re.compile(r"<section\b[^>]*>")
MDIV_OPEN_LINE_RE = re.compile(r"(?m)^[ \t]*<mdiv\b[^>]*>")
MDIV_CLOSE_LINE_RE = re.compile(r"(?m)^[ \t]*</mdiv\s*>[ \t]*(?:\r\n|\n|\r|$)")


@dataclass(frozen=True)
class PieceStart:
    title: str
    anacrusis: int


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attribute(tag: str, name: str) -> str:
    for match in ATTRIBUTE_RE.finditer(tag):
        if match.group("name") == name:
            return match.group("value")
    raise ValueError(f"Missing {name!r} attribute in {tag.strip()!r}")


def _set_attribute(tag: str, name: str, value: str) -> str:
    for match in ATTRIBUTE_RE.finditer(tag):
        if match.group("name") == name:
            return tag[: match.start("value")] + value + tag[match.end("value") :]
    closing = tag.rfind(">")
    return f'{tag[:closing]} {name}="{value}"{tag[closing:]}'


def _load_piece_starts(path: Path) -> dict[tuple[str, int], PieceStart]:
    starts: dict[tuple[str, int], PieceStart] = {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"Title", "page", "accolades", "anacrusis"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"{path} must contain columns: {', '.join(sorted(required))}")
        for line_number, row in enumerate(reader, start=2):
            title = (row["Title"] or "").strip()
            page = (row["page"] or "").strip()
            try:
                accolade = int(row["accolades"])
                anacrusis = int(row["anacrusis"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid page/accolade/anacrusis on CSV line {line_number}") from error
            if not title or not page.isdecimal() or accolade < 1 or anacrusis not in (0, 1):
                raise ValueError(f"Invalid piece start on CSV line {line_number}")
            key = (page, accolade)
            if key in starts:
                raise ValueError(f"Duplicate page/accolade start in {path}: {page}/{accolade}")
            starts[key] = PieceStart(title, anacrusis)
    if not starts:
        raise ValueError(f"No piece starts found in {path}")
    return starts


def _load_page_numbers(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if "number" not in (reader.fieldnames or ()):
            raise ValueError(f"{path} must contain a 'number' column")
        return [(row["number"] or "").strip() for row in reader]


def _validate_source(
    xml_text: str,
) -> tuple[str, str, str, str, set[str], int, str]:
    root = ET.fromstring(xml_text)
    mdivs = [element for element in root.iter() if _local_name(element.tag) == "mdiv"]
    if len(mdivs) != 1:
        raise ValueError(f"Expected one source mdiv, found {len(mdivs)}")
    sections = [element for element in mdivs[0].iter() if _local_name(element.tag) == "section"]
    if len(sections) != 1:
        raise ValueError(f"Expected one section in the source mdiv, found {len(sections)}")

    mdiv_open = MDIV_OPEN_LINE_RE.search(xml_text)
    if not mdiv_open:
        raise ValueError("Could not locate the mdiv opening tag")
    mdiv_close = MDIV_CLOSE_LINE_RE.search(xml_text, mdiv_open.end())
    if not mdiv_close:
        raise ValueError("Could not locate the mdiv closing tag")

    block = xml_text[mdiv_open.start() : mdiv_close.end()]
    section_open = SECTION_OPEN_RE.search(block)
    if not section_open:
        raise ValueError("Could not locate the section opening tag")
    section_close = re.search(r"</section\s*>", block[section_open.end() :])
    if not section_close:
        raise ValueError("Could not locate the section closing tag")
    close_start = section_open.end() + section_close.start()
    content = block[section_open.end() : close_start]
    line_ending_match = re.match(r"\r\n|\n|\r", content)
    if not line_ending_match:
        raise ValueError("Expected a line break after the section opening tag")

    prefix = block[: section_open.end()] + line_ending_match.group()
    suffix = block[close_start:]
    content = content[line_ending_match.end() :]
    first_event = None
    for line in content.splitlines(keepends=True):
        first_event = EVENT_LINE_RE.fullmatch(line.rstrip("\r\n"))
        if first_event:
            break
    if not first_event:
        raise ValueError("No pb, sb, or measure elements found in the section")
    return (
        xml_text[: mdiv_open.start()],
        xml_text[mdiv_close.end() :],
        prefix,
        suffix,
        {element.attrib[XML_ID] for element in root.iter() if XML_ID in element.attrib},
        sum(_local_name(element.tag) == "measure" for element in sections[0].iter()),
        content,
    )


def _new_id(existing_ids: set[str]) -> str:
    while True:
        value = "mdiv_" + "".join(secrets.choice(ID_ALPHABET) for _ in range(26))
        if value not in existing_ids:
            existing_ids.add(value)
            return value


def split_mei_text(
    xml_text: str,
    starts: dict[tuple[str, int], PieceStart],
    page_numbers: list[str],
) -> str:
    """Return MEI text with one titled mdiv for each measure-number reset."""
    validated = _validate_source(xml_text)
    (
        outer_prefix,
        outer_suffix,
        prefix,
        suffix,
        existing_ids,
        expected_measure_count,
        content,
    ) = _validate_source(xml_text)

    newline_match = re.search(r"\r\n|\n|\r", xml_text)
    newline = newline_match.group() if newline_match else "\n"
    first_event = None
    for line in content.splitlines(keepends=True):
        first_event = EVENT_LINE_RE.fullmatch(line.rstrip("\r\n"))
        if first_event:
            break
    if first_event is None:
        raise ValueError("Could not determine section indentation")
    indent = first_event.group("indent")

    pieces: list[tuple[PieceStart, str]] = []
    pending: list[str] = []
    current_lines: list[str] | None = None
    current_start: PieceStart | None = None
    used_starts: set[tuple[str, int]] = set()
    current_page = 0
    printed_page = ""
    accolade = 1
    at_accolade_start = True
    previous_n: int | None = None
    measure_count = 0

    for line in content.splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        event = EVENT_LINE_RE.fullmatch(line_body)
        if event is None:
            if line.strip() or line_body.strip().startswith("<!--"):
                if not line_body.strip().startswith("<!--") or not line_body.strip().endswith("-->"):
                    raise ValueError(f"Unsupported content in section: {line_body.strip()!r}")
            pending.append(line)
            continue

        name = event.group("name")
        tag = line_body.strip()
        if name == "pb":
            try:
                current_page = int(_attribute(tag, "n"))
            except ValueError as error:
                raise ValueError(f"Invalid pb page number: {tag}") from error
            if current_page < 1:
                raise ValueError(f"Invalid pb page number: {current_page}")
            printed_page = (
                page_numbers[current_page - 1] if current_page <= len(page_numbers) else ""
            )
            accolade = 1
            at_accolade_start = True
            pending.append(line)
            continue

        if name == "sb":
            accolade += 1
            at_accolade_start = True
            pending.append(line)
            continue

        measure_count += 1
        try:
            measure_n = int(_attribute(tag, "n"))
        except ValueError as error:
            raise ValueError(f"Invalid measure number: {tag}") from error
        if measure_n < 0:
            raise ValueError(f"Invalid measure number: {measure_n}")

        key = (printed_page, accolade)
        is_reset = previous_n is None or measure_n < previous_n
        is_csv_start = at_accolade_start and key in starts
        if is_reset and not at_accolade_start:
            raise ValueError(
                f"Measure number reset at {printed_page}/{accolade} without a pb or sb boundary"
            )
        if is_reset or is_csv_start:
            if key not in starts:
                raise ValueError(
                    f"No page_music.csv title for reset at page/accolade "
                    f"{printed_page}/{accolade}"
                )
            if key in used_starts:
                raise ValueError(f"Piece start used more than once: {printed_page}/{accolade}")
            piece_start = starts[key]
            expected_n = 0 if piece_start.anacrusis == 1 else 1
            if measure_n != expected_n:
                raise ValueError(
                    f"Measure at {printed_page}/{accolade} starts at {measure_n}; "
                    f"anacrusis requires {expected_n}"
                )

            if current_lines is not None and current_start is not None:
                pieces.append((current_start, "".join(current_lines)))
            pending_text = "".join(pending)
            pending_events = [
                event.group("name")
                for pending_line in pending
                if (event := EVENT_LINE_RE.fullmatch(pending_line.rstrip("\r\n")))
            ]
            if pieces and "pb" not in pending_events:
                if current_page < 1 or not printed_page:
                    raise ValueError(f"No page context for piece at {printed_page}/{accolade}")
                pending_sb_count = pending_events.count("sb")
                missing_accolades = accolade - 1 - pending_sb_count
                if missing_accolades < 0:
                    raise ValueError(f"Unexpected sb markers at {printed_page}/{accolade}")
                context = f'{indent}<pb n="{current_page}"/>{newline}'
                context += f"{indent}<sb/>{newline}" * missing_accolades
                pending_text = context + pending_text

            current_lines = [pending_text, line]
            current_start = piece_start
            used_starts.add(key)
            pending.clear()
        else:
            if current_lines is None:
                raise ValueError(f"Measure {measure_count} appears before the first piece reset")
            current_lines.extend((*pending, line))
            pending.clear()

        previous_n = measure_n
        at_accolade_start = False

    if current_lines is None or current_start is None:
        raise ValueError("No measure resets found")
    current_lines.extend(pending)
    pieces.append((current_start, "".join(current_lines)))

    missing_starts = set(starts) - used_starts
    if missing_starts:
        sample = ", ".join(f"{page}/{acc}" for page, acc in sorted(missing_starts)[:5])
        raise ValueError(f"{len(missing_starts)} CSV piece starts had no measure reset: {sample}")
    if measure_count != expected_measure_count:
        raise ValueError(
            f"Section contained {measure_count} measures; expected {expected_measure_count}"
        )

    opening_match = re.search(r"<mdiv\b[^>]*>", prefix)
    if opening_match is None:
        raise ValueError("Could not locate mdiv attributes")
    movements: list[str] = []
    for number, (piece_start, piece_content) in enumerate(pieces, start=1):
        opening = opening_match.group()
        opening = _set_attribute(opening, "xml:id", _new_id(existing_ids))
        opening = _set_attribute(opening, "n", str(number))
        opening = _set_attribute(opening, "label", escape(
            piece_start.title,
            {'"': "&quot;", "\r": "&#13;", "\n": "&#10;", "\t": "&#9;"},
        ))
        movement_prefix = prefix[: opening_match.start()] + opening + prefix[opening_match.end() :]
        movements.append(movement_prefix + piece_content + suffix)

    output = outer_prefix + "".join(movements) + outer_suffix
    output_root = ET.fromstring(output)
    output_mdivs = [element for element in output_root.iter() if _local_name(element.tag) == "mdiv"]
    output_measures = [
        element for element in output_root.iter() if _local_name(element.tag) == "measure"
    ]
    output_ids = [
        element.attrib[XML_ID] for element in output_root.iter() if XML_ID in element.attrib
    ]
    if len(output_mdivs) != len(starts) or len(output_measures) != measure_count:
        raise ValueError("Generated MEI failed movement/measure count validation")
    if len(output_ids) != len(set(output_ids)):
        raise ValueError("Generated MEI contains duplicate xml:id values")
    return output


def _default_output_path(source: Path) -> Path:
    return source.with_name(f"{source.stem}_movements{source.suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Split a single-mdiv MEI annotation file into one movement per piece."
    )
    parser.add_argument("xml", type=Path, help="Input MEI XML with measure numbers reset per piece")
    parser.add_argument("page_music_csv", type=Path, help="CSV with Title, page, accolades, anacrusis")
    parser.add_argument("page_numbers_csv", type=Path, help="CSV mapping image order to printed page")
    parser.add_argument("-o", "--output", type=Path, help="Output path (default: <input>_movements.xml)")
    parser.add_argument("--force", action="store_true", help="Overwrite the output file if it exists")
    args = parser.parse_args(argv)

    source = args.xml
    output = args.output or _default_output_path(source)
    if source.resolve() == output.resolve():
        parser.error("output must not overwrite the input XML")
    if output.exists() and not args.force:
        parser.error(f"{output} already exists; choose another output or pass --force")

    try:
        raw = source.read_bytes()
        has_bom = raw.startswith(b"\xef\xbb\xbf")
        xml_text = raw.decode("utf-8-sig")
        starts = _load_piece_starts(args.page_music_csv)
        page_numbers = _load_page_numbers(args.page_numbers_csv)
        result = split_mei_text(xml_text, starts, page_numbers)
        encoded = result.encode("utf-8")
        if has_bom:
            encoded = b"\xef\xbb\xbf" + encoded
        output.write_bytes(encoded)
    except (OSError, UnicodeError, csv.Error, ET.ParseError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"Wrote {output} with {len(starts)} movements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
