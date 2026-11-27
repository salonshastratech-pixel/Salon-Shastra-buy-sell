"""Syncing Excel / CSV files kept in Google Drive (not only Google Sheets). No network: Google's answers are faked."""
import pytest

from teamos.domain import connected_sheets as CS
from teamos.domain.errors import ValidationError

KEY = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456"
XLSX = b"PK\x03\x04 fake xlsx zip"


@pytest.fixture
def google(monkeypatch):
    """Map of url-part -> (bytes, content-type) or Exception; records the order of downloads."""
    answers, calls = {}, []

    def fake(url):
        calls.append(url)
        for part, ans in answers.items():
            if part in url:
                if isinstance(ans, Exception):
                    raise ans
                return ans
        raise CS.urllib.error.URLError("no route")
    monkeypatch.setattr(CS, "_download", fake)
    return answers, calls


def test_every_google_link_shape_gives_the_same_file_key():
    for url in (f"https://docs.google.com/spreadsheets/d/{KEY}/edit#gid=0",
                f"https://docs.google.com/spreadsheets/d/{KEY}/edit?usp=sharing&ouid=1&rtpof=true&sd=true",
                f"https://drive.google.com/file/d/{KEY}/view?usp=drive_link",
                f"https://drive.google.com/open?id={KEY}",
                f"https://drive.google.com/uc?export=download&id={KEY}"):
        assert CS.sheet_key(url) == KEY, url
    assert CS.sheet_key("https://example.com/sheet.xlsx") is None
    with pytest.raises(ValidationError) as e:
        CS.fetch("https://onedrive.live.com/x")
    assert "Google Drive file link" in str(e.value.errors)


def test_google_sheet_downloads_as_excel(google):
    answers, calls = google
    answers["/export?format=xlsx"] = (XLSX, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    name, data = CS.fetch(f"https://docs.google.com/spreadsheets/d/{KEY}/edit")
    assert name.endswith(".xlsx") and data == XLSX and len(calls) == 1


def test_excel_kept_in_drive_is_downloaded_directly(google):
    answers, calls = google
    answers["drive.usercontent.google.com"] = (XLSX, "application/octet-stream")
    name, data = CS.fetch(f"https://drive.google.com/file/d/{KEY}/view?usp=sharing")
    assert name.endswith(".xlsx") and data == XLSX and "export?format" not in calls[0]


def test_excel_opened_in_google_editor_falls_back_to_drive_download(google):
    answers, calls = google
    answers["/export?format=xlsx"] = CS.urllib.error.HTTPError("x", 400, "bad", {}, None)   # not a native Sheet
    answers["drive.usercontent.google.com"] = (XLSX, "application/octet-stream")
    name, _ = CS.fetch(f"https://docs.google.com/spreadsheets/d/{KEY}/edit?rtpof=true&sd=true")
    assert name.endswith(".xlsx") and len(calls) == 2


def test_csv_in_drive(google):
    answers, _ = google
    answers["drive.usercontent.google.com"] = (b"Name,Mobile\nRavi,9848022334\n", "text/csv")
    name, data = CS.fetch(f"https://drive.google.com/file/d/{KEY}/view")
    assert name.endswith(".csv") and b"Ravi" in data


def test_private_file_and_old_xls_are_explained(google):
    answers, _ = google
    answers["drive"] = (b"<!DOCTYPE html><html>Sign in - Google Accounts</html>", "text/html; charset=utf-8")
    with pytest.raises(ValidationError) as e:
        CS.fetch(f"https://drive.google.com/file/d/{KEY}/view")
    assert "private" in str(e.value.errors) and "Anyone with the link" in str(e.value.errors)
    answers.clear()
    answers["drive"] = (b"\xd0\xcf\x11\xe0 old excel", "application/vnd.ms-excel")
    with pytest.raises(ValidationError) as e:
        CS.fetch(f"https://drive.google.com/file/d/{KEY}/view")
    assert ".xls" in str(e.value.errors)


def test_connecting_a_drive_excel_link(app, db, login, monkeypatch):
    from test_smart_import import workbook
    monkeypatch.setattr(CS, "fetch", lambda u: ("google-sheet.xlsx", workbook()))
    lead = login("lead@t.test")
    r = lead.post("/imports/sheets", {"name": "Harika's Excel", "link": f"https://drive.google.com/file/d/{KEY}/view"},
                  follow_redirects=True)
    assert "Sheet connected" in r.data.decode()
    assert db.scalar("SELECT sheet_key FROM connected_sheets") == KEY
    # the same file pasted as a different link shape is recognised as already connected
    r = lead.post("/imports/sheets", {"name": "again", "link": f"https://drive.google.com/open?id={KEY}"},
                  follow_redirects=True)
    assert "already connected" in r.data.decode()
    assert "Google Drive Excel" in lead.get("/imports/").data.decode()
