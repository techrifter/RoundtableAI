import io

from docx import Document
from pypdf import PdfReader


def extract_docx_bytes(content: bytes) -> str:
    doc = Document(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_pdf_bytes(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_text_from_upload(filename: str, content: bytes) -> str:
    """Dispatches on file extension for an uploaded JD/resume. Deliberately just
    two formats rather than a generic document-conversion pipeline 
    — .doc, .rtf, images, etc. are out of scope.
    """
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        text = extract_pdf_bytes(content)
    elif lowered.endswith(".docx"):
        text = extract_docx_bytes(content)
    else:
        raise ValueError(f"Unsupported file type: {filename!r}. Please upload a .pdf or .docx file.")

    if not text.strip():
        raise ValueError(f"Couldn't read any text from {filename!r} — it may be a scanned image, not real text.")
    return text
