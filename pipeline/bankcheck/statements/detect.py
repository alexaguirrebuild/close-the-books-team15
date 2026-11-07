"""Work out a statement's format from its content, since the extension can lie."""
from __future__ import annotations

EXTENSION_FORMATS = {".n43": "n43", ".txt": "n43", ".sta": "mt940", ".xml": "camt053"}


def detect_format(head: bytes) -> str | None:
    start = head.lstrip(b"\xef\xbb\xbf \r\n\t")
    if start.startswith((b"<?xml", b"<Document")):
        return "camt053"
    if start.startswith((b"{1:", b":20:")):
        return "mt940"
    if start[:2] == b"11" and start[2:18].isdigit():
        return "n43"
    return None
