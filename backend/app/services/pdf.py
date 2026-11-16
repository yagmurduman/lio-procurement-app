import io
import logging

from pypdf import PageObject, PdfReader

logger = logging.getLogger(__name__)


def _page_text(page: PageObject) -> str:
    # Layout mode keeps table columns aligned, so the model can tell an item
    # number from a quantity. Fall back to plain mode if a page trips it up.
    try:
        return page.extract_text(extraction_mode="layout") or ""
    except Exception:  # noqa: BLE001
        logger.warning("Layout text extraction failed; falling back to plain mode")
        return page.extract_text() or ""


def extract_text(pdf_bytes: bytes) -> str:
    """Extract the text from a vendor-offer PDF.

    Covers the offer formats we handle; the vendors we work with send
    text-based PDFs.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(_page_text(page) for page in reader.pages)
