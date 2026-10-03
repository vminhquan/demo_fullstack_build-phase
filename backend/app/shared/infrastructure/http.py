from urllib.parse import quote


def content_disposition(filename: str) -> str:
    """Build an attachment header that is safe for non-ASCII and quoted filenames (RFC 6266)."""
    fallback = filename.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "").strip()
    return f"attachment; filename=\"{fallback or 'download'}\"; filename*=UTF-8''{quote(filename, safe='')}"
