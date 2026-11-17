#!/usr/bin/env python3
"""Reset MEI measure numbers at piece starts described by the page CSVs."""

from __future__ import annotations

import argparse
import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

if __package__:
    from .split_mei_movements import (
        EVENT_LINE_RE,
        PieceStart,
        _attribute,
        _load_page_numbers,
        _load_piece_starts,
        _set_attribute,
        _validate_source,
    )
else:
    from split_mei_movements import (
        EVENT_LINE_RE,
        PieceStart,
        _attribute,
        _load_page_numbers,
        _load_piece_starts,
        _set_attribute,
        _validate_source,
    )


def reset_measures(
    xml_text: str,
    starts: dict[tuple[str, int], PieceStart],
    page_numbers: list[str],
) -> str:
    """Return a copy with measure n and label restarted for every piece."""
    outer_prefix, outer_suffix, prefix, suffix, _, expected_count, content = (
        _validate_source(xml_text)
    )
    page = 0
    printed_page = ""
    accolade = 1
    at_accolade_start = True
    counter: int | None = None
    used_starts: set[tuple[str, int]] = set()
    result: list[str] = []
    measure_count = 0

    for line in content.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        event = EVENT_LINE_RE.fullmatch(body)
        if event is None:
            if body.strip() and not (
                body.strip().startswith("<!--") and body.strip().endswith("-->")
            ):
                raise ValueError(f"Unsupported content in section: {body.strip()!r}")
            result.append(line)
            continue

        name = event.group("name")
        if name == "pb":
            page = int(_attribute(body, "n"))
            if page < 1:
                raise ValueError(f"Invalid pb page number: {page}")
            printed_page = page_numbers[page - 1] if page <= len(page_numbers) else ""
            accolade = 1
            at_accolade_start = True
        elif name == "sb":
            accolade += 1
            at_accolade_start = True
        else:
            if not printed_page:
                raise ValueError(f"Measure without a mapped printed page after pb n={page}")
            key = (printed_page, accolade)
            if at_accolade_start and key in starts:
                if key in used_starts:
                    raise ValueError(f"Piece start used more than once: {printed_page}/{accolade}")
                counter = 0 if starts[key].anacrusis == 1 else 1
                used_starts.add(key)
            if counter is None:
                raise ValueError(f"No piece start before measure at {printed_page}/{accolade}")
            _attribute(body, "n")
            _attribute(body, "label")
            body = _set_attribute(body, "n", str(counter))
            body = _set_attribute(body, "label", str(counter))
            counter += 1
            measure_count += 1
            at_accolade_start = False
        result.append(body + ending)

    missing_starts = set(starts) - used_starts
    if missing_starts:
        sample = ", ".join(f"{p}/{a}" for p, a in sorted(missing_starts)[:5])
        raise ValueError(f"{len(missing_starts)} CSV piece starts had no measure: {sample}")
    if measure_count != expected_count or not measure_count:
        raise ValueError(
            f"Section contained {measure_count} measures; expected {expected_count}"
        )
    output = outer_prefix + prefix + "".join(result) + suffix + outer_suffix
    ET.fromstring(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset measure numbers at piece starts in a single-mdiv MEI file."
    )
    parser.add_argument("xml", type=Path, help="Input MEI XML")
    parser.add_argument("page_music_csv", type=Path, help="CSV with Title, page, accolades, anacrusis")
    parser.add_argument("page_numbers_csv", type=Path, help="CSV mapping image order to printed page")
    parser.add_argument("-o", "--output", type=Path, help="Output path (default: <input>_copy.xml)")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file")
    args = parser.parse_args(argv)

    output = args.output or args.xml.with_name(f"{args.xml.stem}_copy{args.xml.suffix}")
    inputs = (args.xml, args.page_music_csv, args.page_numbers_csv)
    if any(output.resolve() == path.resolve() for path in inputs) or (
        output.exists()
        and any(path.exists() and output.samefile(path) for path in inputs)
    ):
        parser.error("output must not overwrite an input file")
    if output.exists() and not args.force:
        parser.error(f"{output} already exists; choose another output or pass --force")

    try:
        raw = args.xml.read_bytes()
        starts = _load_piece_starts(args.page_music_csv)
        pages = _load_page_numbers(args.page_numbers_csv)
        result = reset_measures(raw.decode("utf-8-sig"), starts, pages)
        encoded = result.encode("utf-8")
        if raw.startswith(b"\xef\xbb\xbf"):
            encoded = b"\xef\xbb\xbf" + encoded
        output.write_bytes(encoded)
    except (OSError, UnicodeError, csv.Error, ET.ParseError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(f"Wrote {output} with measure numbers reset for {len(starts)} pieces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
