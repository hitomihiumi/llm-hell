"""Turning a spreadsheet into something a language model can read.

A sheet is the one source here whose meaning is **positional**. A cell says
`X` and nothing else; what it means comes from the row label to its left and
the header rows above it, and those are joined by nothing but column position.

That survives none of the treatment prose gets. `manage_sheets` renders a row
as pipe-delimited cells with the trailing empties dropped, and `excerpt_around`
then collapses every run of whitespace - so a Gantt chart arrived at the answer
model as one long line of `| | | | X | O` with its header rows cut off by the
snippet budget. Asked "according to the gantt chart, when was the team object
3d printed", the model answered from the wrong columns, because aligning them
meant counting pipe characters across fifty rows.

So the alignment is done here instead, by writing every filled cell as
`column=value`:

    R4:  B=Task  C=1  D=8  E=12  F=15  G=18  H=19 ...
    R31: B=3D print parts for the Team Object  G=X  H=O

Nothing is counted any more - `G` is looked up, not arrived at - and the same
question is now answered "September 18". The transformation is lossless and
makes no guess about which rows are headers or what the marks mean: it only
gives every value the address it already had.
"""

import re

# `R 4:  | Task | 1 | 8` - the row form manage_sheets prints. The number is
# the sheet's own 1-based row, which is worth keeping: it is what a person
# sees in the left margin of the real spreadsheet.
_ROW = re.compile(r"^R\s*(\d+):(.*)$")

# The report's prose tail - "Next steps", "Session context" and a block of
# example JSON for writing to the sheet. None of it is the document, all of it
# was landing in the answer prompt, and it was crowding the grid out of the
# snippet budget.
_TAIL = re.compile(r"^-{3,}\s*$")

# `## 'Fall Semester'!A1:AL1000` - which sheet and which range was read.
_RANGE = re.compile(r"^##\s*(.+)$")

# `- **Spring Semester** (sheetId: 298147837) - 1013 rows x 26 cols`, from the
# `### Sheets (3)` block of a manage_sheets `get`. A workbook's tabs are the
# one part of it a range read cannot reach: a read without a sheet name gets
# the FIRST tab and nothing says the others exist.
_TAB = re.compile(r"^-\s+\*\*(.+?)\*\*\s*\(sheetId:")

# How many rows of the top of the sheet are kept when the whole thing will not
# fit. The header band is the only place a column's meaning is written down,
# and it is small - the sheet this was built against uses three rows, for the
# week, the month and the day. Kept deliberately tight: on a cramped budget a
# generous guess here eats the rows that were actually asked about.
HEADER_ROWS = 4

# Legends live at the bottom ("X = Completed Tasks, O = Milestone Deadlines"),
# and a mark whose meaning was cut is a mark that cannot be read.
FOOTER_ROWS = 2

# The least a tab may be given when several share one budget. Enough for the
# range line, the header band and a couple of rows - a tab reduced to nothing
# would be indistinguishable from a tab that does not exist.
TAB_FLOOR_CHARS = 400

_ELLIPSIS = "…"


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, the way a spreadsheet names its columns."""
    name = ""
    while True:
        name = chr(ord("A") + index % 26) + name
        index = index // 26 - 1
        if index < 0:
            return name


def parse_report(report: str) -> tuple[str, list[tuple[int, list[str]]]]:
    """The range heading and the sheet's rows, from a manage_sheets report.

    Rows arrive as `(row number, cells)` with the cells positional - an empty
    string is a real empty cell and holds its column open.
    """
    heading = ""
    rows: list[tuple[int, list[str]]] = []
    for line in (report or "").splitlines():
        stripped = line.strip()
        if _TAIL.match(stripped):
            break
        if not heading:
            found = _RANGE.match(stripped)
            if found:
                heading = found.group(1).strip()
                continue
        match = _ROW.match(stripped)
        if match:
            rows.append((int(match.group(1)), [cell.strip() for cell in match.group(2).split("|")]))
    return heading, rows


def _fill_forward(cells: list[str], width: int) -> list[str]:
    """A header row with its merged cells written out.

    A calendar header writes the month once, over the fortnight it covers:
    `R3: C=Jan  E=Feb  I=Mar`. Column K carries no month of its own - it
    inherits March from column I, and that inheritance is the whole reason a
    reader has to count columns to place a mark.
    """
    out: list[str] = []
    carried = ""
    for index in range(width):
        value = cells[index].strip() if index < len(cells) else ""
        if value:
            carried = value
        out.append(carried)
    return out


def _filled(cells: list[str], width: int) -> int:
    return sum(1 for cell in cells[:width] if cell.strip())


def _occupied(rows: list[tuple[int, list[str]]]) -> list[tuple[int, list[str]]]:
    """The sheet without its blank rows.

    Real sheets start with one - a spacer above the title, or a frozen row
    left empty - and it is invisible in the rendered output because empty rows
    are dropped there too. Counted as part of the header band, though, it is a
    row in which every column is empty, so every column fails the "the band
    addresses this column" test and nothing resolves at all. Which is exactly
    what happened: the resolution worked on a hand-built sheet and did nothing
    on the real one.
    """
    return [(number, cells) for number, cells in rows if any(cell.strip() for cell in cells)]


def band_size(rows: list[tuple[int, list[str]]]) -> int:
    """How many of the leading rows are header rather than data.

    The band ends at its last *dense* row. A merged row - the month written
    once per fortnight - is sparse, and belongs to the band only because a
    dense row of dates follows it. A task row is sparse too, and follows
    nothing, which is what separates the two: `HEADER_ROWS` alone cannot,
    and a band that swallowed one task row would hang that task's own mark on
    every column as though it meant something.
    """
    rows = _occupied(rows)
    band = [cells for _, cells in rows[:HEADER_ROWS]]
    width = max((len(cells) for cells in band), default=0)
    size = 0
    for position, cells in enumerate(band):
        if _filled(cells, width) * 2 > width:
            size = position + 1
    return size


def _looks_like_grid(rows: list[tuple[int, list[str]]], size: int, width: int) -> bool:
    """Whether this sheet is a matrix to be read by position, or a table.

    Two things have to hold, and both are about emptiness.

    The band must have a **merged row** - a month written once over the
    fortnight it covers, a quarter written once per season - because a row
    that addresses spans rather than columns is what makes a mark's column
    something a reader has to count to.

    The body must be **sparse**: a Gantt row is a label and one or two marks
    in an otherwise empty line. A table's rows are full.

    Both, because either alone misfires. Getting this wrong in the permissive
    direction is what costs: a contact list read as a grid would hang three
    strangers' names off every name in it.
    """
    if size < 2 or width < 4:
        return False
    band = [cells for _, cells in rows[:size]]
    if not any(2 <= _filled(cells, width) <= width // 2 for cells in band):
        return False
    body = [cells for _, cells in rows[size:]]
    if not body:
        return False
    sparse = sum(1 for cells in body if _filled(cells, width) <= width // 2)
    return sparse * 2 >= len(body)


def column_context(rows: list[tuple[int, list[str]]]) -> dict[int, list[str]]:
    """What each column means, read down the header band.

    A Gantt cell says `X` and nothing else. Which week that `X` falls in is
    written at the top of the sheet, in a band whose merged cells make the
    column it belongs to a matter of counting - across sixteen columns, in an
    image, in a model's head. That count is arithmetic we already hold the
    inputs for, so it is done here: column K is week 9, March, the 16th, and
    saying so costs one lookup.

    Returns `{column index: [value from each band row]}` for the columns the
    band fully addresses, and `{}` when the top of the sheet is not a header
    band at all.
    """
    rows = _occupied(rows)
    size = band_size(rows)
    band = [cells for _, cells in rows[:size]]
    width = max((len(cells) for cells in band), default=0)
    if not _looks_like_grid(rows, size, width):
        return {}
    filled = [_fill_forward(cells, width) for cells in band]
    context: dict[int, list[str]] = {}
    for index in range(width):
        values = [row[index] for row in filled]
        if all(values):
            context[index] = values
    return context


def render_row(number: int, cells: list[str], context: dict[int, list[str]] | None = None) -> str:
    """One row as `Rn: B=label  G=X`, or "" when the row is empty.

    With a `context`, a cell whose column the header band addresses carries
    that meaning inline - `H=X [6 | Feb | 23]` - so nothing downstream has to
    align it against a band that may be forty rows away, or cropped out of the
    picture entirely. The row's own label is left bare: it is what the columns
    are being read against, not something they explain.
    """
    positions = [index for index, cell in enumerate(cells) if cell]
    label = positions[0] if positions else None
    parts = []
    for index in positions:
        piece = f"{column_letter(index)}={cells[index]}"
        meaning = (context or {}).get(index) if index != label else None
        if meaning:
            piece += " [" + " | ".join(meaning) + "]"
        parts.append(piece)
    return f"R{number}: " + "  ".join(parts) if parts else ""


def render_rows(rows: list[tuple[int, list[str]]]) -> list[tuple[int, str]]:
    """Every non-empty row, addressed and resolved against the header band.

    The band rows themselves are rendered bare: annotating a header with
    itself says nothing, and `C=Jan [1 | Jan | 19]` reads as a fourth header
    row.
    """
    context = column_context(rows)
    rows = _occupied(rows)
    size = band_size(rows)
    out = []
    for position, (number, cells) in enumerate(rows):
        line = render_row(number, cells, None if position < size else context)
        if line:
            out.append((number, line))
    return out


def render(report: str) -> str:
    """The whole sheet, addressed. Empty rows are dropped, order is kept."""
    heading, rows = parse_report(report)
    lines = [f"sheet: {heading}"] if heading else []
    lines.extend(line for _, line in render_rows(rows))
    return "\n".join(lines)


def excerpt(report: str, query: str, limit: int) -> str:
    """The sheet, trimmed to about `limit` characters around what was asked.

    Not `excerpt_around`, and the difference is the point: a window over a
    grid keeps whichever rows happen to be adjacent, and the rows that give a
    cell its meaning are never adjacent - they are at the very top of the
    sheet and at the very bottom.

    So rows are kept by what they are worth rather than by where they sit.
    The matching rows come first: they are why the hit exists, and a header
    band with nothing under it answers nothing. Then the header band, without
    which a matching row is a bare `X`. Then the legend, which says what the
    mark means. Whatever budget survives goes to the rest, in order.
    """
    heading, rows = parse_report(report)
    rendered = render_rows(rows)
    if not rendered:
        return ""

    head = [f"sheet: {heading}"] if heading else []
    whole = "\n".join(head + [line for _, line in rendered])
    if len(whole) <= limit:
        return whole

    terms = [word for word in re.split(r"\W+", (query or "").lower()) if len(word) >= 3]

    def score(position: int) -> int:
        lowered = rendered[position][1].lower()
        return sum(1 for term in terms if term in lowered)

    matching = sorted(
        (position for position in range(len(rendered)) if score(position)),
        key=lambda position: (-score(position), position),
    )
    header = list(range(min(HEADER_ROWS, len(rendered))))
    footer = list(range(max(0, len(rendered) - FOOTER_ROWS), len(rendered)))
    rest = list(range(len(rendered)))

    keep: set[int] = set()
    size = len("\n".join(head))
    for position in [*matching, *header, *footer, *rest]:
        if position in keep:
            continue
        cost = len(rendered[position][1]) + 1
        if size + cost > limit:
            continue
        keep.add(position)
        size += cost

    lines = list(head)
    previous: int | None = None
    for position in sorted(keep):
        if previous is not None and position != previous + 1:
            lines.append(_ELLIPSIS)
        lines.append(rendered[position][1])
        previous = position
    return "\n".join(lines)


def parse_tab_names(metadata: str) -> list[str]:
    """The workbook's sheet names, from a manage_sheets `get` report.

    Worth its own function because reading only the first tab is invisible:
    the answer is confident, cites the file, and is about the wrong half of
    the year. "Wisco Wingmen Gantt Chart" holds Fall Semester, Spring
    Semester and a condensed view, and a question about March was answered
    from the September tab.
    """
    return [
        match.group(1).strip()
        for match in (_TAB.match(line.strip()) for line in (metadata or "").splitlines())
        if match
    ]


def a1_range(tab: str, columns: str = "A1:AZ1000") -> str:
    """`'Spring Semester'!A1:AZ1000`, with the quoting A1 notation needs.

    A name is always quoted rather than only when it has to be - a bare
    `Sheet1!A1` works, but so does the quoted form, and deciding when a name
    is safe to leave bare is a rule this does not need.
    """
    return "'{}'!{}".format(tab.replace("'", "''"), columns)


def excerpt_tabs(reports: list[str], query: str, limit: int) -> str:
    """Several tabs of one workbook, sharing `limit` between them.

    Budget goes where the question points. Tabs are served in order of how
    many rows in them match, so a question about March is not squeezed out by
    the tab that happens to be first, and each tab keeps a floor so that a tab
    which does not fit still contributes its header band rather than nothing.

    Reassembled in the workbook's own order, because a reader comparing this
    against the real spreadsheet should find the tabs where they live.
    """
    usable = [(index, report) for index, report in enumerate(reports) if report]
    if not usable:
        return ""
    if len(usable) == 1:
        return excerpt(usable[0][1], query, limit)

    terms = [word for word in re.split(r"\W+", (query or "").lower()) if len(word) >= 3]

    def matches(report: str) -> int:
        lowered = report.lower()
        return sum(1 for term in terms if term in lowered)

    order = sorted(usable, key=lambda pair: (-matches(pair[1]), pair[0]))
    pieces: dict[int, str] = {}
    remaining = limit
    for served, (index, report) in enumerate(order):
        left = len(order) - served - 1
        share = max(TAB_FLOOR_CHARS, remaining - TAB_FLOOR_CHARS * left)
        piece = excerpt(report, query, share)
        if piece:
            pieces[index] = piece
            remaining -= len(piece) + 1
    return "\n\n".join(pieces[index] for index in sorted(pieces))


def render_tabs(reports: list[str]) -> str:
    """Every tab, whole, in the workbook's order."""
    return "\n\n".join(piece for piece in (render(report) for report in reports) if piece)
