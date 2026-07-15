"""Google Sheets tracker sync.

Appends alerted jobs as rows to a user-owned spreadsheet; the Status and
Notes columns belong to the human and are never written by us after the
initial blank. Auth is a Google service account that the sheet is shared
with (Editor).
"""

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER = ["First seen", "Company", "Title", "Location", "Link", "Status", "Notes"]


def append_jobs(service_account_info: dict, sheet_id: str, rows: list[list[str]]) -> None:
    creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    ws = gspread.authorize(creds).open_by_key(sheet_id).sheet1
    if not ws.acell("A1").value:
        ws.append_row(HEADER)
    ws.append_rows(rows, value_input_option="USER_ENTERED")


def sheet_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"
