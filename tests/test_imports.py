"""Spreadsheet import (FR-004, UAT-03): preview, validation, duplicates, never overwrite."""
import io
import re

from conftest import SELLER_FORM


CSV = ("Owner Name,Mobile No,Salon Name,City,Area,Campaign,Asking Price,Salon Type\n"
       "Anil Kumar,9811111111,Anil Hair Studio,Hyderabad,Ameerpet,Oct FB,12L,Gents\n"
       "Anil K,+91 98111 11111,Anil Hair Studio 2,Hyderabad,Ameerpet,Oct FB,,\n"      # repeated mobile in file
       "Test Owner,9876543210,Test Salon,Hyderabad,Kondapur,,,\n"                      # already in CRM
       "No Phone,,Nameless Salon,Hyderabad,Miyapur,,,\n"                                # invalid
       "Bad Price,9822222222,Price Salon,Hyderabad,Miyapur,,lots,\n"                    # invalid price
       "Meena Rao,9833333333,Meena Beauty Lounge,Hyderabad,Banjara Hills,Referral,,ladies\n")


def upload(c, content=CSV, name="leads.csv"):
    return c.c.post("/imports/", data={"csrf_token": c.csrf, "file": (io.BytesIO(content.encode()), name)},
                    content_type="multipart/form-data")


def test_finance_cannot_import_team_can(login):
    assert upload(login("fin@t.test")).status_code == 403
    assert upload(login("seller@t.test")).status_code != 403  # D-85: team = everything except Finance


def test_uat03_import_preview_flags_duplicates_and_invalid_without_writing(login, db):
    login("seller@t.test").post("/sellers/new", SELLER_FORM)           # Test Owner already exists
    lead = login("lead@t.test")
    r = upload(lead)
    assert r.status_code == 302
    bid = int(re.search(r"/imports/(\d+)", r.headers["Location"]).group(1))
    statuses = [x["status"] for x in db.all("SELECT status FROM import_rows WHERE batch_id = ? ORDER BY row_no", (bid,))]
    assert statuses == ["new", "duplicate_file", "duplicate_crm", "invalid", "invalid", "new"]
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 1          # nothing written at preview
    page = lead.get(f"/imports/{bid}").data
    assert b"Already in CRM" in page and b"Repeated in file" in page
    # auto-mapping recognised the headers
    mapping = db.scalar("SELECT mapping FROM import_batches WHERE id = ?", (bid,))
    assert '"Owner Name": "full_name"' in mapping and '"Area": "locality"' in mapping


def test_commit_creates_only_new_rows_with_provenance_and_keeps_existing_untouched(login, db):
    login("seller@t.test").post("/sellers/new", SELLER_FORM)
    lead = login("lead@t.test")
    bid = int(re.search(r"/imports/(\d+)", upload(lead).headers["Location"]).group(1))
    lead.post(f"/imports/{bid}/commit", {"first_task_due": "2030-01-05T10:00"})
    assert db.scalar("SELECT status FROM import_batches") == "committed"
    assert db.scalar("SELECT created_count FROM import_batches") == 2
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 3
    meena = db.one("SELECT o.*, l.gender_format FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                   "JOIN seller_listings l ON l.opportunity_id = o.id WHERE c.full_name = 'Meena Rao'")
    assert meena["source"] == "import" and f"Import #{bid}" in meena["source_detail"]
    assert meena["gender_format"] == "women"                            # 'ladies' synonym mapped
    anil = db.one("SELECT l.asking_price FROM seller_listings l JOIN opportunities o ON o.id = l.opportunity_id "
                  "JOIN contacts c ON c.id = o.contact_id WHERE c.full_name = 'Anil Kumar'")
    assert anil["asking_price"] == 1_200_000
    # existing contact not overwritten; imported leads have no contact consent
    assert db.scalar("SELECT full_name FROM contacts WHERE phone_normalized = '+919876543210'") == "Test Owner"
    assert db.scalar("SELECT COUNT(*) FROM contact_consents WHERE source = 'staff_entry:import' AND granted = 1") == 0
    # first-contact tasks honour chosen time and normal priority
    assert db.scalar("SELECT COUNT(*) FROM tasks WHERE task_type = 'first_contact' AND priority = 'normal' "
                     "AND due_at = '2030-01-05T04:30:00Z'") == 2
    # raw rows preserved
    assert "Gents" in db.scalar("SELECT raw FROM import_rows WHERE row_no = 2 AND batch_id = ?", (bid,))
    # cannot commit twice
    lead.post(f"/imports/{bid}/commit", {})
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 3


def test_duplicate_decisions_link_or_flag(login, db):
    login("seller@t.test").post("/sellers/new", SELLER_FORM)
    lead = login("lead@t.test")
    bid = int(re.search(r"/imports/(\d+)", upload(lead).headers["Location"]).group(1))
    rows = {r["status"]: r["id"] for r in db.all("SELECT id, status FROM import_rows WHERE batch_id = ?", (bid,))}
    lead.post(f"/imports/{bid}/commit", {f"decision_{rows['duplicate_crm']}": "link",
                                         f"decision_{rows['duplicate_file']}": "flag",
                                         f"decision_{rows['invalid']}": "create"})  # not allowed → skipped
    assert db.scalar("SELECT COUNT(*) FROM contacts WHERE phone_normalized = '+919876543210'") == 1
    assert db.scalar("SELECT COUNT(*) FROM opportunities o JOIN contacts c ON c.id = o.contact_id "
                     "WHERE c.phone_normalized = '+919876543210'") == 2      # linked as a second opportunity
    assert db.scalar("SELECT COUNT(*) FROM duplicate_reviews WHERE status = 'open'") == 1  # flagged file duplicate
    assert db.scalar("SELECT COUNT(*) FROM contacts c WHERE c.full_name IN ('No Phone','Bad Price')") == 0


def test_remap_rechecks_rows_and_required_columns_enforced(login, db):
    lead = login("lead@t.test")
    content = "Who,Number,Shop,Town,Zone\nRaj,9844444444,Raj Cuts,Hyderabad,Uppal\n"
    bid = int(re.search(r"/imports/(\d+)", upload(lead, content).headers["Location"]).group(1))
    assert db.scalar("SELECT status FROM import_rows") == "invalid"          # nothing auto-mapped
    lead.post(f"/imports/{bid}/commit", {})
    assert db.scalar("SELECT status FROM import_batches") == "preview"       # blocked: required unmapped
    lead.post(f"/imports/{bid}/mapping", {"map_0": "full_name", "map_1": "phone", "map_2": "salon_name",
                                          "map_3": "city", "map_4": "locality"})
    assert db.scalar("SELECT status FROM import_rows") == "new"
    lead.post(f"/imports/{bid}/commit", {})
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 1


def test_row_that_became_duplicate_after_preview_is_not_created(login, db):
    lead = login("lead@t.test")
    content = "Name,Mobile,Salon,City,Locality\nLate Dup,9855555555,Late Salon,Hyderabad,Uppal\n"
    bid = int(re.search(r"/imports/(\d+)", upload(lead, content).headers["Location"]).group(1))
    login("seller@t.test").post("/sellers/new", {**SELLER_FORM, "phone": "9855555555"})
    lead.post(f"/imports/{bid}/commit", {})
    assert db.scalar("SELECT outcome FROM import_rows") == "skipped"         # re-classified as duplicate at commit
    assert db.scalar("SELECT COUNT(*) FROM opportunities") == 1


def test_xlsx_upload(login, db):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Name", "Mobile", "Salon", "City", "Locality", "Chairs"])
    ws.append(["Xl Owner", 9866666666, "Excel Salon", "Hyderabad", "Kukatpally", 6])
    buf = io.BytesIO()
    wb.save(buf)
    lead = login("lead@t.test")
    r = lead.c.post("/imports/", data={"csrf_token": lead.csrf, "file": (io.BytesIO(buf.getvalue()), "a.xlsx")},
                    content_type="multipart/form-data")
    bid = int(re.search(r"/imports/(\d+)", r.headers["Location"]).group(1))
    lead.post(f"/imports/{bid}/commit", {})
    assert db.scalar("SELECT chairs FROM seller_listings") == 6
    assert db.scalar("SELECT phone_normalized FROM contacts") == "+919866666666"


def test_rejects_unsupported_file(login, db):
    r = upload(login("lead@t.test"), "x", "notes.txt")
    assert r.status_code == 302 and db.scalar("SELECT COUNT(*) FROM import_batches") == 0
