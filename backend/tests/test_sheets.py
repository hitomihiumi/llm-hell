"""Reading a spreadsheet, whose meaning is where a value sits.

Everything here comes from one real sheet - a team's Gantt chart, columns are
dates and cells are `X` - and one real failure. Asked "according to the gantt
chart when was the team object 3d printed", the answer model named the wrong
columns, because the row arrived as `| | | | X | O` with its dates cut off by
the snippet budget and its line breaks collapsed. With the rows addressed the
same model answers "September 18", which is what the sheet says.
"""

from app.services import sheets

# Trimmed from the real manage_sheets report, boilerplate included: the tail
# was landing in the answer prompt and taking up half the budget.
REPORT = """## 'Fall Semester'!A1:AL1000

**Rows:** 51 | **Columns:** 30 | **Major dimension:** ROWS

R 1:
R 2:  | Week | 1 | 2 |  | 3
R 3:  |  | Sep
R 4:  | Task | 1 | 8 | 12 | 15 | 18 | 19
R 5:  | Propulsion systems
R 6:  | Motor sizing and selection |  |  |  |  | X
R 8:  | Wings & Support Structures
R 9:  | Research Wing Support Structures |  |  | X
R10:  | Design Inital Rib Support |  |  | X
R11:  | Resarch Airfoils |  |  |  | X
R26:  | Team Object |  |  |  |  |  | O
R30:  | Machine the mount for the Team Object |  |  |  | X
R31:  | 3D print parts for the Team Object |  |  |  |  | X | O
R39:  | Proof of Concept |  |  | X
R51:  | X = Completed Tasks, O = Milestone Deadlines

---
**Next steps:**
- Write values to a range: `manage_sheets` - `{"operation":"updateValues"}`

---
**Session context** (someone@example.com):
- No new unread emails since session start
"""


def test_a_cell_carries_its_column():
    """The whole fix. `X` in the seventh column is `G=X`, and G is looked up
    in the header row rather than counted to."""
    rendered = sheets.render(REPORT)

    assert "R31: B=3D print parts for the Team Object  G=X  H=O" in rendered
    assert "R4: B=Task  C=1  D=8  E=12  F=15  G=18  H=19" in rendered


def test_the_servers_own_prose_is_not_the_document():
    """ "Next steps" and "Session context" are the MCP server talking about
    itself. They were reaching the answer prompt and crowding out the grid."""
    rendered = sheets.render(REPORT)

    assert "Next steps" not in rendered
    assert "updateValues" not in rendered
    assert "unread emails" not in rendered


def test_the_range_says_which_sheet_was_read():
    assert sheets.render(REPORT).startswith("sheet: 'Fall Semester'!A1:AL1000")


def test_an_empty_row_is_not_a_line():
    rendered = sheets.render(REPORT)

    assert "R1:" not in rendered


def test_columns_keep_counting_past_z():
    assert sheets.column_letter(0) == "A"
    assert sheets.column_letter(25) == "Z"
    assert sheets.column_letter(26) == "AA"
    assert sheets.column_letter(51) == "AZ"
    assert sheets.column_letter(52) == "BA"


def test_a_report_with_no_rows_renders_nothing():
    assert sheets.render("no grid here") == ""
    assert sheets.excerpt("no grid here", "anything", 100) == ""


# --- trimming ---------------------------------------------------------------


def test_a_sheet_that_fits_is_kept_whole():
    whole = sheets.excerpt(REPORT, "3d print", 10_000)

    assert whole == sheets.render(REPORT)


def test_trimming_keeps_the_header_band():
    """A window centred on the matching row would take its neighbours, and
    the rows that say what a column means are nowhere near it - they are at
    the top of the sheet."""
    trimmed = sheets.excerpt(REPORT, "3d print parts", 320)

    assert len(trimmed) <= 400
    assert "R4: B=Task" in trimmed
    assert "R3: B=Sep" in trimmed or "R3: C=Sep" in trimmed


def test_trimming_keeps_the_legend():
    """`X` means completed and `O` means a deadline, and that is written once,
    in the last row. A mark whose legend was cut cannot be read."""
    trimmed = sheets.excerpt(REPORT, "3d print parts", 320)

    assert "Completed Tasks" in trimmed


def test_trimming_keeps_the_row_that_was_asked_about():
    trimmed = sheets.excerpt(REPORT, "3d print parts", 320)

    assert "3D print parts for the Team Object  G=X" in trimmed


def test_a_gap_in_the_rows_is_marked():
    """Row numbers are the sheet's own, so a jump is visible anyway - but an
    ellipsis says it was our trimming rather than an empty row."""
    trimmed = sheets.excerpt(REPORT, "3d print parts", 320)

    assert "…" in trimmed


def test_the_row_asked_about_outranks_the_header_band():
    """On a budget too small for both, the matching row wins. A header band
    with nothing under it describes columns and answers nothing."""
    trimmed = sheets.excerpt(REPORT, "3d print parts", 130)

    assert "3D print parts for the Team Object" in trimmed
