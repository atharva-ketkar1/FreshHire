"""Google Sheets tracker sync.

Appends alerted jobs as rows to a user-owned spreadsheet; the Status and
Notes columns belong to the human and are never written by us after the
initial blank. Auth is a Google service account that the sheet is shared
with (Editor).
"""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER = ["First seen (CT)", "Company", "Title", "Location", "Link", "Applied", "Result", "Notes"]
LOCAL_TZ = ZoneInfo("America/Chicago")

_HYPERLINK_URL_RE = re.compile(r'HYPERLINK\("([^"]+)"')


def fmt_local(iso_utc: str) -> str:
    """'2026-07-15T18:38:12Z' -> '2026-07-15 1:38 PM' in Central time."""
    dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %-I:%M %p")
RESULT_OPTIONS = [
    "Online assessment", "Phone screen", "Interview",
    "Onsite", "Offer", "Rejected", "No response",
]


def job_row(first_seen: str, company: str, title: str, location: str, url: str,
            notes: str = "") -> list[str]:
    """One sheet row. Link is a clean 'Apply' hyperlink; Applied renders as an
    unchecked checkbox (the column has checkbox validation)."""
    link = '=HYPERLINK("{}","Apply")'.format(url.replace('"', ""))
    return [first_seen, company, title, location, link, "FALSE", "", notes]


def apply_row_validation(spreadsheet, worksheet_id: int, start_row: int, end_row: int) -> None:
    """Checkbox on Applied (F) and dropdown on Result (G) for rows
    [start_row, end_row], 1-based inclusive. Applied per data row rather than
    to the whole column so empty rows stay visually empty."""
    def _range(col):
        return {"sheetId": worksheet_id, "startRowIndex": start_row - 1, "endRowIndex": end_row,
                "startColumnIndex": col, "endColumnIndex": col + 1}

    spreadsheet.batch_update({"requests": [
        {"setDataValidation": {"range": _range(5), "rule": {
            "condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True}}},
        {"setDataValidation": {"range": _range(6), "rule": {
            "condition": {"type": "ONE_OF_LIST",
                          "values": [{"userEnteredValue": v} for v in RESULT_OPTIONS]},
            "strict": False, "showCustomUi": True}}},
    ]})


def append_jobs(service_account_info: dict, sheet_id: str, rows: list[list[str]]) -> int:
    """Append rows the sheet doesn't already have. Strictly additive: rows are
    inserted below the existing table (INSERT_ROWS — never the API's default
    overwrite mode) and existing rows are never touched. Returns rows added."""
    creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    ss = gspread.authorize(creds).open_by_key(sheet_id)
    ws = ss.sheet1
    if not ws.acell("A1").value:
        ws.append_row(HEADER)

    # duplicate guard: skip jobs whose ATS link is already present, so a stale
    # database checkout can't double-append
    existing_urls = {
        m.group(1)
        for row in ws.get("E2:E100000", value_render_option="FORMULA")
        for cell in row
        if (m := _HYPERLINK_URL_RE.search(str(cell)))
    }
    fresh = [r for r in rows if _HYPERLINK_URL_RE.search(r[4]).group(1) not in existing_urls]
    if not fresh:
        return 0

    resp = ws.append_rows(
        fresh,
        value_input_option="USER_ENTERED",
        insert_data_option="INSERT_ROWS",
        table_range="A1",
    )
    # e.g. "'Sheet1'!A7:H9" -> rows 7..9 get the checkbox/dropdown validation
    updated = resp["updates"]["updatedRange"].split("!")[1]
    start, end = (re.search(r"\d+", part).group() for part in updated.split(":"))
    apply_row_validation(ss, ws.id, int(start), int(end))
    return len(fresh)


def sheet_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"
