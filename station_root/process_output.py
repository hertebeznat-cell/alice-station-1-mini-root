"""Decode Linux UTF-8 and Windows/WSL UTF-16 output, including mixed streams."""

from __future__ import annotations

import io


def _encoding(data):
    if data.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if data.startswith(b"\n\x00"):
        return "utf-16-le"
    if data.startswith(b"\x00\n"):
        return "utf-16-be"
    end = data.find(b"\n")
    sample = data[: min(192, end + 1 if end >= 0 else len(data))]
    even, odd = sample[::2], sample[1::2]
    if b"\x00" in sample:
        return "utf-16-le" if odd.count(0) >= even.count(0) else "utf-16-be"
    # A Russian UTF-16 line need not contain NUL until its first space.
    if len(odd) >= 2 and sum(value <= 8 for value in odd) >= len(odd) * 0.75:
        return "utf-16-le"
    if len(even) >= 2 and sum(value <= 8 for value in even) >= len(even) * 0.75:
        return "utf-16-be"
    return "utf-8-sig"


def iter_output(stream):
    """Yield decoded lines; choose encoding again at every line boundary.

    WSL can emit a UTF-16 host warning followed by UTF-8 Linux output. Reading
    it as a text pipe loses both the encoding and UTF-16 newline boundaries.
    """
    pending = b""
    read = getattr(stream, "read1", stream.read)
    while True:
        chunk = read(4096)
        if chunk:
            pending += chunk
        final = not chunk
        while pending and (len(pending) >= 4 or final):
            encoding = _encoding(pending)
            newline = {"utf-16-le": b"\n\x00", "utf-16-be": b"\x00\n"}.get(encoding, b"\n")
            end = pending.find(newline)
            if encoding.startswith("utf-16"):
                while end >= 0 and end % 2:
                    end = pending.find(newline, end + 1)
            if end < 0:
                if not final:
                    break
                end = len(pending)
            else:
                end += len(newline)
            line, pending = pending[:end], pending[end:]
            yield line.decode(encoding, errors="replace").lstrip("\ufeff")
        if final:
            break


def decode_output(data):
    if isinstance(data, str):
        return data
    return "".join(iter_output(io.BytesIO(data)))
