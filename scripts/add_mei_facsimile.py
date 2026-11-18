#!/usr/bin/env python3
"""Add facsimile zones from the measure annotations to MEI files."""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

MEI_NS = "http://www.music-encoding.org/ns/mei"
XML_NS = "http://www.w3.org/XML/1998/namespace"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_ID = f"{{{XML_NS}}}id"
NAMESPACE_PREFIXES = {XML_NS: "xml", XLINK_NS: "xlink"}
OUTPUT_SUFFIX = "_facsimile"
ATTRIBUTE_RE = re.compile(r"""(?<![\w:.-])(?P<name>[\w:.-]+)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""", re.S)
MARKUP_RE = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|"
    r"<(?P<name>measure|music)(?=[\s/>])(?:[^<>\"']|\"[^\"]*\"|'[^']*')*>",
    re.S,
)
NOTE_ITEM_RE = re.compile(r"(?P<start>\d+)(?:\s*-\s*(?P<end>\d+))?")


@dataclass(frozen=True)
class Mapping:
    title: str
    notes: str


@dataclass(frozen=True)
class Annotations:
    pieces: dict[str, dict[str, list[str]]]
    surfaces: list[ET.Element]
    zone_ids: set[str]
    graphic_base: Path


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attribute_name(name: str) -> str:
    if not name.startswith("{"):
        return name
    namespace, local = name[1:].split("}", 1)
    if namespace not in NAMESPACE_PREFIXES:
        raise ValueError(f"Unsupported namespaced attribute in facsimile: {name}")
    return f"{NAMESPACE_PREFIXES[namespace]}:{local}"


def _quote_attribute(value: str) -> str:
    return escape(value, {'"': "&quot;", "\r": "&#13;", "\n": "&#10;", "\t": "&#9;"})


def _set_attribute(tag: str, name: str, value: str) -> str:
    value = _quote_attribute(value)
    for match in ATTRIBUTE_RE.finditer(tag):
        if match.group("name") == name:
            return tag[: match.start("value")] + value + tag[match.end("value") :]
    closing = len(tag) - 2 if tag.endswith("/>") else len(tag) - 1
    while closing > 0 and tag[closing - 1].isspace():
        closing -= 1
    return f'{tag[:closing]} {name}="{value}"{tag[closing:]}'


def expand_notes(notes: str) -> list[str]:
    """Expand a measure list such as ``0-7,9,13`` while preserving its order."""
    measures: list[str] = []
    for item in notes.split(","):
        item = item.strip()
        match = NOTE_ITEM_RE.fullmatch(item)
        if not match:
            raise ValueError(f"Invalid notes item {item!r}; use numbers or ranges like 0-7")
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        if end < start:
            raise ValueError(f"Invalid descending measure range {item!r}")
        measures.extend(str(number) for number in range(start, end + 1))
    return measures


def _load_mappings(path: Path) -> dict[str, Mapping]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"filename", "Title", "notes"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"{path} must contain columns: {', '.join(sorted(required))}")
        mappings: dict[str, Mapping] = {}
        for line_number, row in enumerate(reader, start=2):
            filename = (row["filename"] or "").strip()
            title = (row["Title"] or "").strip()
            if not filename or not title:
                raise ValueError(f"Missing filename or Title on CSV line {line_number}")
            if filename in mappings:
                raise ValueError(f"Duplicate filename on CSV line {line_number}: {filename}")
            mappings[filename] = Mapping(title, (row["notes"] or "").strip())
    if not mappings:
        raise ValueError(f"No MEI mappings found in {path}")
    return mappings


def _load_annotations(path: Path, graphic_base: Path | None) -> Annotations:
    root = ET.parse(path).getroot()
    music = next((child for child in root if _local_name(child.tag) == "music"), None)
    facsimile = None if music is None else next(
        (child for child in music if _local_name(child.tag) == "facsimile"), None
    )
    if facsimile is None:
        raise ValueError(f"{path} has no music/facsimile element")

    surfaces = [child for child in facsimile if _local_name(child.tag) == "surface"]
    zone_ids: set[str] = set()
    for surface in surfaces:
        for element in surface:
            if _local_name(element.tag) == "zone":
                zone_id = element.get(XML_ID)
                if not zone_id or zone_id in zone_ids:
                    raise ValueError(f"Missing or duplicate zone xml:id in {path}: {zone_id!r}")
                zone_ids.add(zone_id)

    pieces: dict[str, dict[str, list[str]]] = {}
    for mdiv in root.iter(f"{{{MEI_NS}}}mdiv"):
        label = (mdiv.get("label") or "").strip()
        if not label or label in pieces:
            raise ValueError(f"Missing or duplicate mdiv label in {path}: {label!r}")
        measures: dict[str, list[str]] = {}
        for measure in mdiv.iter(f"{{{MEI_NS}}}measure"):
            number = measure.get("n")
            facs = (measure.get("facs") or "").split()
            if number is None or number in measures or not facs:
                raise ValueError(f"Invalid measure n/facs in annotation piece {label!r}")
            for reference in facs:
                if not reference.startswith("#") or reference[1:] not in zone_ids:
                    raise ValueError(f"Unknown zone reference {reference!r} in {label!r}")
            measures[number] = facs
        pieces[label] = measures
    if not pieces:
        raise ValueError(f"No annotated mdiv elements found in {path}")
    return Annotations(pieces, surfaces, zone_ids, graphic_base or path.parent)


def _target_path(target: str, base: Path, output: Path, search_parents: bool) -> str:
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or target.startswith(("/", "#")) or Path(target).is_absolute():
        return target
    candidates = [base, *base.parents] if search_parents else [base]
    image = next((folder / target for folder in candidates if (folder / target).is_file()), None)
    if image is None:
        if search_parents:
            raise ValueError(f"Cannot find graphic target {target!r}; use --graphic-base")
        image = base / target
    relative = os.path.relpath(image.resolve(), output.parent.resolve())
    return PurePosixPath(*Path(relative).parts).as_posix()


def _serialize(
    element: ET.Element,
    indent: str,
    unit: str,
    newline: str,
    output: Path,
    graphic_base: Path,
    search_parents: bool,
) -> str:
    if element.tag.startswith("{") and not element.tag.startswith(f"{{{MEI_NS}}}"):
        raise ValueError(f"Unsupported non-MEI element in facsimile: {element.tag}")
    attributes = dict(element.attrib)
    if _local_name(element.tag) == "graphic" and "target" in attributes:
        attributes["target"] = _target_path(attributes["target"], graphic_base, output, search_parents)
    rendered = "".join(
        f' {_attribute_name(name)}="{_quote_attribute(value)}"' for name, value in attributes.items()
    )
    name = _local_name(element.tag)
    if (element.text or "").strip() or any((child.tail or "").strip() for child in element):
        raise ValueError(f"Mixed text content is not supported in facsimile element {name}")
    if len(element) == 0:
        return f"{indent}<{name}{rendered}/>{newline}"
    children = "".join(
        _serialize(child, indent + unit, unit, newline, output, graphic_base, search_parents)
        for child in element
    )
    return f"{indent}<{name}{rendered}>{newline}{children}{indent}</{name}>{newline}"


def add_facsimile(
    xml_text: str,
    mapping: Mapping,
    annotations: Annotations,
    output: Path,
    search_graphic_parents: bool = True,
) -> str:
    """Return MEI text with measure facs links and the referenced facsimile zones."""
    root = ET.fromstring(xml_text)
    if root.find(f".//{{{MEI_NS}}}facsimile") is not None:
        raise ValueError("Input MEI already contains a facsimile element")
    musics = list(root.iter(f"{{{MEI_NS}}}music"))
    if len(musics) != 1:
        raise ValueError(f"Expected one music element, found {len(musics)}")
    measures = list(root.iter(f"{{{MEI_NS}}}measure"))
    if not measures:
        raise ValueError("Input MEI contains no measure elements")
    if mapping.title not in annotations.pieces:
        raise ValueError(f"No annotation mdiv labeled {mapping.title!r}")

    if mapping.notes:
        annotation_numbers = expand_notes(mapping.notes)
    else:
        annotation_numbers = [measure.get("n") for measure in measures]
        if None in annotation_numbers:
            raise ValueError("Measures without n require the CSV notes field")
        if len(annotation_numbers) != len(set(annotation_numbers)):
            raise ValueError("Repeated measure numbers require the CSV notes field")
    if len(annotation_numbers) != len(measures):
        raise ValueError(
            f"Notes list {len(annotation_numbers)} measures; MEI contains {len(measures)}"
        )

    piece = annotations.pieces[mapping.title]
    missing = [number for number in annotation_numbers if number not in piece]
    if missing:
        raise ValueError(f"Annotation piece has no measure(s): {', '.join(missing[:10])}")
    facs_values = [" ".join(piece[number]) for number in annotation_numbers]
    used_zones = {reference[1:] for value in facs_values for reference in value.split()}
    existing_ids = {element.get(XML_ID) for element in root.iter() if element.get(XML_ID)}
    conflicts = existing_ids & (used_zones | {
        element.get(XML_ID)
        for surface in annotations.surfaces
        if any(child.get(XML_ID) in used_zones for child in surface)
        for element in surface.iter()
        if element.get(XML_ID)
    })
    if conflicts:
        raise ValueError(f"Facsimile xml:id already used in MEI: {sorted(conflicts)[0]}")

    pieces: list[str] = []
    position = 0
    measure_index = 0
    music_seen = False
    for match in MARKUP_RE.finditer(xml_text):
        name = match.group("name")
        if not name:
            continue
        tag = match.group()
        if name == "measure":
            pieces.append(xml_text[position : match.start()])
            pieces.append(_set_attribute(tag, "facs", facs_values[measure_index]))
            position = match.end()
            measure_index += 1
        else:
            if tag.endswith("/>"):
                raise ValueError("The music element must contain body content")
            music_seen = True
    if measure_index != len(measures) or not music_seen:
        raise ValueError("Could not match measure/music tags in the MEI text")
    pieces.append(xml_text[position:])
    measured_text = "".join(pieces)

    music_match = next(
        match for match in MARKUP_RE.finditer(measured_text) if match.group("name") == "music"
    )
    music_end = music_match.end()
    newline_match = re.search(r"\r\n|\n|\r", xml_text)
    newline = newline_match.group() if newline_match else "\n"
    line_start = max(measured_text.rfind("\n", 0, music_match.start()), measured_text.rfind("\r", 0, music_match.start())) + 1
    music_indent = re.match(r"[ \t]*", measured_text[line_start:]).group()
    child_match = re.match(r"(?:\r\n|\n|\r)([ \t]*)<", measured_text[music_end:])
    if child_match and child_match.group(1).startswith(music_indent) and len(child_match.group(1)) > len(music_indent):
        child_indent = child_match.group(1)
    else:
        child_indent = music_indent + "  "
    unit = child_indent[len(music_indent) :]

    surfaces: list[ET.Element] = []
    for surface in annotations.surfaces:
        children = [
            child for child in surface
            if _local_name(child.tag) != "zone" or child.get(XML_ID) in used_zones
        ]
        if any(_local_name(child.tag) == "zone" for child in children):
            copy = ET.Element(surface.tag, surface.attrib)
            copy.extend(children)
            surfaces.append(copy)
    facsimile = ET.Element(f"{{{MEI_NS}}}facsimile")
    facsimile.extend(surfaces)
    block = _serialize(
        facsimile,
        child_indent,
        unit,
        newline,
        output,
        annotations.graphic_base,
        search_graphic_parents,
    )
    result = measured_text[:music_end] + newline + block.rstrip("\r\n") + measured_text[music_end:]

    output_root = ET.fromstring(result)
    output_ids = [element.get(XML_ID) for element in output_root.iter() if element.get(XML_ID)]
    output_zones = {zone.get(XML_ID) for zone in output_root.iter(f"{{{MEI_NS}}}zone")}
    output_facs = [measure.get("facs") for measure in output_root.iter(f"{{{MEI_NS}}}measure")]
    if len(output_ids) != len(set(output_ids)):
        raise ValueError("Generated MEI contains duplicate xml:id values")
    if output_facs != facs_values or output_zones != used_zones:
        raise ValueError("Generated MEI failed facsimile validation")
    return result


def _expand_inputs(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern)) if glob.has_magic(pattern) else [pattern]
        if not matches:
            raise ValueError(f"No files match {pattern!r}")
        for match in matches:
            path = Path(match)
            if glob.has_magic(pattern) and path.stem.endswith(OUTPUT_SUFFIX):
                continue
            if path not in paths:
                paths.append(path)
    if not paths:
        raise ValueError("No MEI input files to process")
    return paths


def _output_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}{OUTPUT_SUFFIX}{path.suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create *_facsimile.mei copies with measure zones from measure annotations."
    )
    parser.add_argument("mei", nargs="+", help="Input MEI file or glob, e.g. MEI/*.mei")
    parser.add_argument("measure_annotations", type=Path, help="measure_annotations.xml")
    parser.add_argument("mei_pages_csv", type=Path, help="CSV with filename, Title, notes")
    parser.add_argument(
        "--graphic-base",
        type=Path,
        help="Directory for relative graphic targets (default: search annotation folder and parents)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing output files")
    args = parser.parse_args(argv)

    try:
        inputs = _expand_inputs(args.mei)
        mappings = _load_mappings(args.mei_pages_csv)
        annotations = _load_annotations(args.measure_annotations, args.graphic_base)
    except (OSError, UnicodeError, csv.Error, ET.ParseError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    failures = 0
    protected = [args.measure_annotations, args.mei_pages_csv, *inputs]
    for source in inputs:
        output = _output_path(source)
        try:
            if source.stem.endswith(OUTPUT_SUFFIX):
                raise ValueError("input is already a facsimile output")
            if any(output.resolve() == path.resolve() for path in protected):
                raise ValueError("output would overwrite an input file")
            if output.exists() and not args.force:
                raise ValueError(f"{output} already exists; pass --force to replace it")
            if source.name not in mappings:
                raise ValueError(f"{source.name} is not listed in {args.mei_pages_csv}")
            raw = source.read_bytes()
            result = add_facsimile(
                raw.decode("utf-8-sig"),
                mappings[source.name],
                annotations,
                output,
                args.graphic_base is None,
            )
            encoded = result.encode("utf-8")
            if raw.startswith(b"\xef\xbb\xbf"):
                encoded = b"\xef\xbb\xbf" + encoded
            output.write_bytes(encoded)
            print(f"Wrote {output}")
        except (OSError, UnicodeError, csv.Error, ET.ParseError, ValueError) as error:
            failures += 1
            print(f"error: {source}: {error}", file=sys.stderr)

    print(f"Processed {len(inputs) - failures} of {len(inputs)} MEI files.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
