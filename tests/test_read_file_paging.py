"""read_file: owner-password PDFs, UTF-16 text and offset/limit paging (synthetic files)."""
from __future__ import annotations

import io

import pytest

from service.tools import builtin


def _pdf(text: str, *, algorithm: str | None = None, user: str = "", owner: str = "owner-pw") -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    if algorithm:
        writer.encrypt(user_password=user, owner_password=owner, algorithm=algorithm)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_owner_password_pdf_with_an_empty_user_password_is_read(tmp_path):
    path = tmp_path / "restricted.pdf"
    path.write_bytes(_pdf("Quarterly planning notes", algorithm="RC4-128"))
    out = builtin.read_file(str(path))
    assert "Quarterly planning notes" in out
    assert "password-protected" not in out


def test_pdf_that_needs_a_real_user_password_is_still_refused(tmp_path):
    path = tmp_path / "locked.pdf"
    path.write_bytes(_pdf("Hidden words", algorithm="RC4-128", user="user-pw"))
    out = builtin.read_file(str(path))
    assert "password-protected" in out and "Hidden words" not in out


@pytest.mark.parametrize("codec", ["utf-16", "utf-16-le", "utf-16-be"])
def test_utf16_text_is_read_not_rejected_as_binary(tmp_path, codec):
    path = tmp_path / "notes.txt"
    path.write_bytes(("Meeting moved to 3pm\nBring the invoice\n" * 3).encode(codec))
    out = builtin.read_file(str(path))
    assert "Meeting moved to 3pm" in out and "Bring the invoice" in out
    assert "not text" not in out


def test_a_real_binary_file_is_still_refused(tmp_path):
    path = tmp_path / "blob.dat"
    path.write_bytes(bytes(range(256)) * 8)
    assert "not text" in builtin.read_file(str(path))


def _long_text(tmp_path, chars: int):
    path = tmp_path / "long.txt"
    body = "".join(f"{i:06d}\n" for i in range(chars // 7))
    path.write_text(body)
    return path, body


def test_read_file_pages_through_a_long_text_file_with_offset_and_limit(tmp_path):
    path, body = _long_text(tmp_path, 40_000)
    first = builtin.read_file(str(path))
    assert first.startswith(body[:builtin.MAX_OUTPUT]) and "offset=12000" in first
    second = builtin.read_file(str(path), offset=12_000, limit=5_000)
    assert body[12_000:17_000] in second and "offset=17000" in second
    assert body[:100] not in second
    tail = builtin.read_file(str(path), offset=len(body) - 50, limit=5_000)
    assert body[-50:] in tail and "offset=" not in tail


def test_paging_reaches_text_beyond_the_first_read_window(tmp_path):
    path, body = _long_text(tmp_path, 200_000)
    out = builtin.read_file(str(path), offset=150_000, limit=2_000)
    assert body[150_000:152_000] in out and "offset=152000" in out


def test_paging_a_long_extracted_document(tmp_path):
    from docx import Document
    document = Document()
    for i in range(1500):
        document.add_paragraph(f"Paragraph number {i:05d} of the report.")
    path = tmp_path / "report.docx"
    document.save(str(path))
    first = builtin.read_file(str(path))
    assert "offset=" in first
    later = builtin.read_file(str(path), offset=12_000, limit=3_000)
    assert "Paragraph number" in later and "Paragraph number 00000" not in later


def test_offset_past_the_end_is_reported(tmp_path):
    path = tmp_path / "short.txt"
    path.write_text("tiny")
    assert "past the end" in builtin.read_file(str(path), offset=500)


# --- tools audit 4.2 ---

def test_move_path_allows_a_case_only_rename(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    out = builtin.move_path(str(tmp_path / "notes.txt"), str(tmp_path / "Notes.txt"))
    assert out.startswith("moved")
    assert "Notes.txt" in {p.name for p in tmp_path.iterdir()}


def test_move_path_moves_a_broken_symlink_and_reports_a_missing_source(tmp_path):
    import os
    os.symlink(tmp_path / "gone", tmp_path / "dangling")
    assert builtin.move_path(str(tmp_path / "dangling"), str(tmp_path / "moved")).startswith("moved")
    assert os.path.islink(tmp_path / "moved")
    assert "no such path" in builtin.move_path(str(tmp_path / "absent"), str(tmp_path / "x"))


def test_move_path_reports_a_permission_denied_source_as_such(tmp_path, monkeypatch):
    import os
    def denied(path):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(builtin.os, "lstat", denied)
    out = builtin.move_path(str(tmp_path / "a"), str(tmp_path / "b"))
    assert "no such path" not in out and "permission" in out.lower()


def test_run_shell_branch_has_no_stdin(monkeypatch):
    import inspect
    src = inspect.getsource(builtin.run_shell)
    assert src.count("stdin=subprocess.DEVNULL") >= 2


def test_list_dir_blocked_notice_survives_truncation(tmp_path, monkeypatch):
    for i in range(1500):
        (tmp_path / f"file_with_a_long_name_{i:05d}.txt").write_text("")
    real_walk = builtin.os.walk
    def walk(p, onerror=None):
        if onerror:
            onerror(PermissionError(1, "denied", "/private/area"))
        yield from real_walk(p)
    monkeypatch.setattr(builtin.os, "walk", walk)
    out = builtin.list_dir(str(tmp_path), recursive=True)
    assert out.startswith("…[1 subfolder(s) skipped") and "listing cut" in out
    assert len(out) <= builtin.MAX_OUTPUT + 200


def test_xlsx_reader_closes_the_workbook(tmp_path, monkeypatch):
    from openpyxl import Workbook
    path = tmp_path / "a.xlsx"
    wb = Workbook(); wb.active.append(["a", 1]); wb.save(str(path))
    import openpyxl
    closed = []
    real = openpyxl.load_workbook
    def spy(*a, **k):
        book = real(*a, **k)
        original = book.close
        book.close = lambda: (closed.append(1), original())[1]
        return book
    monkeypatch.setattr(openpyxl, "load_workbook", spy)
    assert "a | 1" in builtin.read_file(str(path)) and closed


def test_pptx_reader_reads_table_cells_and_grouped_shapes(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(4), Inches(1)).table
    for r, row in enumerate((("Item", "Qty"), ("Widgets", "42"))):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    group = slide.shapes.add_group_shape()
    box = group.shapes.add_textbox(Inches(1), Inches(4), Inches(3), Inches(1))
    box.text_frame.text = "Grouped caption"
    path = tmp_path / "d.pptx"
    deck.save(str(path))
    out = builtin.read_file(str(path))
    assert "Widgets | 42" in out and "Grouped caption" in out
