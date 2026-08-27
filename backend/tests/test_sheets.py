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

    # The marks now carry what their column means as well - see the
    # resolution tests below - but the addressing is the property here.
    assert "R31: B=3D print parts for the Team Object  G=X [3 | Sep | 18]" in rendered
    assert "H=O [3 | Sep | 19]" in rendered
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


# --- the tabs -----------------------------------------------------------------

# A workbook's `get` report. The tab list is the only place the other sheets
# are named: a range read with no sheet name returns the first one and says
# nothing about the rest.
METADATA = """## Wisco Wingmen Gantt Chart

**Spreadsheet ID:** 1fSpOH
**Locale:** en_US

### Sheets (3)
- **Fall Semester** (sheetId: 0) - 1027 rows x 38 cols
- **Spring Semester** (sheetId: 298147837) - 1013 rows x 26 cols
- **Condensed Gantt Chart** (sheetId: 251364932) - 978 rows x 31 cols

---
**Next steps:**
- Read a range: `manage_sheets`
"""

SPRING = """## 'Spring Semester'!A1:Z1000

R 2:  | Week | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9
R 3:  |  | Jan |  | Feb |  |  |  | Mar
R 4:  | Task | 19 | 26 | 2 | 9 | 16 | 23 | 2 | 9 | 16
R26:  | Landing Gear - Design |  |  |  |  |  |  |  |  | X
R40:  | X = Completed Tasks, O = Milestone Deadlines
"""


def test_the_other_tabs_are_found():
    """The failure this fixes: asked when the landing gear was designed -
    March, on the spring tab - the answer came from the September tab, which
    is the only one a range read without a sheet name ever returns."""
    assert sheets.parse_tab_names(METADATA) == [
        "Fall Semester",
        "Spring Semester",
        "Condensed Gantt Chart",
    ]


def test_a_report_with_no_tab_list_names_none():
    assert sheets.parse_tab_names(REPORT) == []
    assert sheets.parse_tab_names("") == []


def test_a_tab_becomes_a_range():
    assert sheets.a1_range("Spring Semester") == "'Spring Semester'!A1:AZ1000"


def test_an_apostrophe_in_a_tab_name_is_doubled():
    """A1 notation escapes a quote by doubling it. Without this, a sheet
    called "Bob's plan" makes a range the API rejects."""
    assert sheets.a1_range("Bob's plan").startswith("'Bob''s plan'!")


def test_every_tab_reaches_the_answer():
    both = sheets.excerpt_tabs([REPORT, SPRING], "landing gear design", 10_000)

    assert "3D print parts for the Team Object" in both
    assert "Landing Gear - Design" in both


def test_the_tab_the_question_is_about_is_served_first():
    """Budget follows the question. Otherwise the tab that happens to come
    first in the workbook crowds out the one that answers."""
    tight = sheets.excerpt_tabs([REPORT, SPRING], "landing gear design", 700)

    assert "Landing Gear - Design" in tight


def test_a_crowded_tab_still_contributes_its_header_band():
    """Reduced to nothing, a tab is indistinguishable from a tab that does
    not exist - and its absence is invisible in the answer."""
    tight = sheets.excerpt_tabs([REPORT, SPRING], "landing gear design", 700)

    assert "Fall Semester" in tight


def test_tabs_come_back_in_the_workbooks_order():
    """Served by relevance, reassembled by position: a reader checking this
    against the real spreadsheet should find the tabs where they live."""
    both = sheets.excerpt_tabs([REPORT, SPRING], "landing gear design", 10_000)

    assert both.index("Fall Semester") < both.index("Spring Semester")


def test_one_tab_is_just_the_sheet():
    assert sheets.excerpt_tabs([REPORT], "3d print", 10_000) == sheets.excerpt(REPORT, "3d print", 10_000)
    assert sheets.excerpt_tabs([], "3d print", 10_000) == ""


def test_rendering_every_tab_keeps_them_apart():
    whole = sheets.render_tabs([REPORT, SPRING])

    assert whole.count("sheet: ") == 2


# --- resolving a column against the header band -------------------------------
#
# A Gantt cell says `X`. Which week that is lives at the top of the sheet, in a
# band whose merged cells make the answer a matter of counting columns - which
# is exactly what kept going wrong: right task, right tab, wrong week by one.
# It is arithmetic, and these pin it down.


def gantt(*extra_rows: str) -> str:
    """A Spring-Semester-shaped sheet: weeks, merged months, dates, then tasks."""
    weeks = " | ".join(["", "Week"] + [str(n) for n in range(1, 16)])
    months = " | ".join(["", "", "Jan", "", "Feb", "", "", "", "Mar", "", "", "", "", "Apr"])
    dates = " | ".join(["", "Task", "19", "26", "2", "9", "16", "23", "2", "9", "16", "23", "30", "6", "13"])
    head = ["## 'Spring Semester'!A1:Z1000", "R2: " + weeks, "R3: " + months, "R4: " + dates]
    return chr(10).join(head + list(extra_rows))


def test_a_mark_carries_the_week_month_and_date_of_its_column():
    """The whole point: `H` is week 6, February, the 23rd, and nobody has to
    count to find that out."""
    rendered = sheets.render(gantt("R6: | Update Wing Design | | | | | | X"))
    assert "H=X [6 | Feb | 23]" in rendered


def test_a_merged_month_reaches_the_columns_it_covers():
    """February is written once, over the fortnight it spans. A column that
    carries no month of its own still falls in one."""
    rendered = sheets.render(gantt("R9: | Optimize Manufacturing | | | | | | | | | X"))
    # K is four columns past the last written month, and the month is March.
    assert "K=X [9 | Mar | 16]" in rendered


def test_the_row_label_is_left_bare():
    """`B` is what the columns are being read against. Annotating it with the
    band's own first column says nothing and reads as noise."""
    rendered = sheets.render(gantt("R6: | Update Wing Design | | | | | | X"))
    assert "B=Update Wing Design  H=" in rendered


def test_the_band_rows_are_not_annotated_with_themselves():
    """`C=Jan [1 | Jan | 19]` reads as a fourth header row."""
    rendered = sheets.render(gantt("R6: | Wings | | | | | | X"))
    assert "R3: C=Jan  E=Feb" in rendered


def test_a_task_row_is_never_taken_for_a_header_row():
    """`HEADER_ROWS` is a window, not a measurement. A sheet whose band is
    three rows would otherwise pull the first task in - and hang that task's
    own mark on every column as though it meant something."""
    rendered = sheets.render(gantt("R6: | Update Wing Design | | | | | | X"))
    assert "| X]" not in rendered


def test_a_plain_table_is_left_alone():
    """The permissive failure is the one that costs. A contact list read as a
    grid would hang three strangers' names off every name in it."""
    rows = [
        ["Name", "Email", "Team", "City"],
        ["Bob", "b@example.com", "Air", "Kyiv"],
        ["Cara", "c@example.com", "Fuel", "Lviv"],
        ["Dan", "d@example.com", "Air", "Odesa"],
        ["Eve", "e@example.com", "Fuel", "Kyiv"],
    ]
    report = chr(10).join(
        ["## 'People'!A1:D100"] + [f"R{number}: " + " | ".join(cells) for number, cells in enumerate(rows, start=1)]
    )
    assert "[" not in sheets.render(report)


def test_a_sheet_with_no_merged_row_is_left_alone():
    """Merging is the signal. Without it, no column needs resolving - every
    header names exactly the column it sits in."""
    rows = [
        ["", "Task", "Mon", "Tue", "Wed", "Thu", "Fri"],
        ["", "Standup", "", "", "X", "", ""],
        ["", "Review", "", "", "", "", "X"],
    ]
    report = chr(10).join(
        ["## 'Week'!A1:G100"] + [f"R{number}: " + " | ".join(cells) for number, cells in enumerate(rows, start=1)]
    )
    assert "[" not in sheets.render(report)


def test_an_excerpt_carries_the_resolution_too():
    """A trimmed sheet is where this matters most - the band may not have
    survived the budget, and a bare `K=X` with no band above it is unreadable."""
    body = [f"R{n}: | Task {n} | | | | | | | | | X" for n in range(6, 60)]
    trimmed = sheets.excerpt(gantt(*body), "Task 40", 900)
    assert "K=X [9 | Mar | 16]" in trimmed


def test_a_blank_row_above_the_header_does_not_disable_resolution():
    """Real sheets start with one - a spacer, or a frozen row left empty - and
    it is invisible in the output because blank rows are dropped there too.
    Counted into the band, it is a row where every column is empty, so every
    column fails "the band addresses this" and nothing resolves. Which is what
    happened: this worked on a hand-built sheet and did nothing on the real one.
    """
    blank = chr(10).join(["## 'Spring Semester'!A1:Z1000", "R1: | | |"])
    rendered = sheets.render(blank + chr(10) + gantt("R6: | Update Wing Design | | | | | | X").split(chr(10), 1)[1])
    assert "H=X [6 | Feb | 23]" in rendered
