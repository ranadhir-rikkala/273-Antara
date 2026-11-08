import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ingest.services import CorruptDocumentError


class PypdfPages:
    def pages(self, data: bytes) -> list[str]:
        try:
            return [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]
        except PdfReadError as error:
            raise CorruptDocumentError(str(error)) from error
