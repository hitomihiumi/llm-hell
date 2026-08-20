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

# How many rows of the top of the sheet are kept when the whole thing will not
# fit. The header band is the only place a column's meaning is written down,
# and it is small - the sheet this was built against uses three rows, for the
# week, the month and the day. Kept deliberately tight: on a cramped budget a
# generous guess here eats the rows that were actually asked about.
HEADER_ROWS = 4

# Legends live at the bottom ("X = Completed Tasks, O = Milestone Deadlines"),
# and a mark whose meaning was cut is a mark that cannot be read.
FOOTER_ROWS = 2

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


def render_row(number: int, cells: list[str]) -> str:
    """One row as `Rn: B=label  G=X`, or "" when the row is empty."""
    filled = [f"{column_letter(index)}={cell}" for index, cell in enumerate(cells) if cell]
    return f"R{number}: " + "  ".join(filled) if filled else ""


def render(report: str) -> str:
    """The whole sheet, addressed. Empty rows are dropped, order is kept."""
    heading, rows = parse_report(report)
    lines = [f"sheet: {heading}"] if heading else []
    lines.extend(line for line in (render_row(number, cells) for number, cells in rows) if line)
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
    rendered = [(number, render_row(number, cells)) for number, cells in rows]
    rendered = [(number, line) for number, line in rendered if line]
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
