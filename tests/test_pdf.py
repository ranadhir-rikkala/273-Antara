import io

import pytest
from pypdf import PdfWriter

from ingest.pdf import PypdfPages
from ingest.services import CorruptDocumentError


def blank_pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pypdf_returns_one_text_per_page() -> None:
    assert PypdfPages().pages(blank_pdf(2)) == ["", ""]


def test_pypdf_reports_a_broken_file_as_a_corrupt_document() -> None:
    with pytest.raises(CorruptDocumentError):
        PypdfPages().pages(b"not a pdf")
