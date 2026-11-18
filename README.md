# Anhaltisches Choralbuch digital

[![CC BY 4.0][cc-by-image]][cc-by]

Authors: [Axel Berndt](https://github.com/axelberndt) ([Paderborn University](https://www.uni-paderborn.de/person/55565)), [Thomas Weber](https://github.com/th-we) ([Notengrafik Berlin GmbH](https://notengrafik.com/)), [Aida Amiryan-Stein](https://github.com/AidaAmst) ([Paderborn University](https://www.uni-paderborn.de/person/89520))

### About

We present a critical music edition of the whole "Anhaltisches Choralbuch" (English: "Anhalt Chorale Book"). It is a collection of four-part harmonizations of hymns from the hymnals commonly used in the German Duchy of Anhalt around 1900. All harmonizations were composed by [Rudolf Haase](https://imslp.org/wiki/Category:Haase,_Rudolf) (1841-1916). The book was printed by [Breitkopf & Härtel](https://www.breitkopf.com/) in Leipzig and published by [Verlag Otto Schulze](https://d-nb.info/gnd/1072982439) in Köthen, Germany. The main source for the encodings was the [2nd edition](/facsimile/) from 1901/02.

### Resetting MEI measure numbers

To number measures from zero for pieces with an anacrusis and from one otherwise:

```shell
python scripts/reset_mei_measure_numbers.py path/to/input.xml facsimile/page_music.csv facsimile/page_numbers.csv
```

The input must have a single `mdiv` and section. Piece starts come from the
printed page and accolade in `page_music.csv`, mapped from one-based `pb` indices
using `page_numbers.csv`. The script updates both `n` and `label`, and writes
`<input>_copy.xml` without changing the source. Use `--output` to select another
destination or `--force` to replace an existing output.

### Splitting MEI movement annotations

To create one movement per piece from an MEI annotation file whose measure numbers
already reset at piece boundaries and which contains one source `mdiv`, run:

```shell
python scripts/split_mei_movements.py path/to/input.xml facsimile/page_music.csv facsimile/page_numbers.csv
```

The script writes `<input>_movements.xml` beside the input. It uses the
`page_music.csv` titles and anacrusis values, maps each one-based `pb` page index
through `page_numbers.csv`, generates unique `mdiv_` IDs, and leaves the input
unchanged. Use `--output` to choose another destination; an existing output
requires `--force`.

### Adding facsimile links to MEI files

To copy one MEI file, or all files matched by a quoted glob, with measure-zone
links from the annotation map, run:

```shell
python scripts/add_mei_facsimile.py "MEI/*.mei" MEI/measure_annotations.xml MEI/MEI_pages.csv
```

The script writes `<input>_facsimile.mei` beside each input and skips existing
`_facsimile` files when expanding a glob. `MEI_pages.csv` maps each MEI filename
to the matching annotation `mdiv` label. Its `notes` field optionally lists the
annotation measures encoded by the MEI file in order, such as `0-7,9,13`; when it
is empty, MEI measure `n` values are used and must be unique. Every measure gets
a `facs` reference, and `music` gets a `facsimile` containing only the referenced
zones. Use `--graphic-base` if image paths cannot be found automatically, or
`--force` to replace existing outputs.

### License
[![CC BY 4.0][cc-by-shield]][cc-by]

This work is licensed under [Creative Commons Attribution 4.0 International License][cc-by].

[cc-by]: http://creativecommons.org/licenses/by/4.0/
[cc-by-image]: https://i.creativecommons.org/l/by/4.0/88x31.png
[cc-by-shield]: https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg

### Funding Statement

This edition is supported by KreativInstitut.OWL, a consortium consisting of OWL University of Applied Sciences and Arts, Detmold University of Music, and Paderborn University, funded by the Ministry of Economic Affairs, Industry, Climate Action and Energy of the State of North Rhine-Westphalia, Germany.

