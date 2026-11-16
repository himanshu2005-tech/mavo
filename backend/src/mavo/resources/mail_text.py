"""Bounded local MIME text extraction. No attachments, scripts or remote fetches."""

import base64
from html.parser import HTMLParser

from mavo.google_auth import GoogleConnectionError


class _VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.chunks = []
        self.size = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "head"}:
            self.hidden.append(tag)
        if not self.hidden and tag in {"br", "p", "div", "li", "tr", "h1", "h2"}:
            self.handle_data("\n")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        elif not self.hidden and tag in {"p", "div", "li", "tr"}:
            self.handle_data("\n")

    def handle_data(self, data):
        if not self.hidden and self.size < 24000:
            data = data[: 24000 - self.size]
            self.chunks.append(data)
            self.size += len(data)


def looks_like_html(text):
    head = text[:600].lstrip().casefold()
    return head.startswith(("<!doctype", "<html", "<head", "<body", "<table", "<div")) or (
        text.count("</") > 8 and text.count("<") > len(text) / 200
    )


def message_text(payload):
    parts, plain, markup = [payload], [], []
    visited = 0
    while parts and visited < 100:
        visited += 1
        part = parts.pop()
        if not isinstance(part, dict) or not isinstance(part.get("parts", []), list):
            raise GoogleConnectionError("Google returned an invalid message body")
        parts.extend(reversed(part.get("parts", [])[:30]))
        body = part.get("body", {})
        if not isinstance(body, dict):
            raise GoogleConnectionError("Google returned an invalid message body")
        data, mime = body.get("data"), part.get("mimeType")
        if mime not in {"text/plain", "text/html"} or data is None:
            continue
        if not isinstance(data, str):
            raise GoogleConnectionError("Google returned an invalid message body")
        # Named files are attachments, even when their MIME type happens to be text/plain.
        if part.get("filename") or len(plain) + len(markup) >= 20:
            continue
        try:
            encoded = data[:90000]
            value = base64.b64decode(
                (encoded + "=" * (-len(encoded) % 4)).encode("ascii"),
                altchars=b"-_",
                validate=True,
            ).decode("utf-8", errors="replace")[:24000]
        except (ValueError, UnicodeError):
            raise GoogleConnectionError("Google returned an unreadable message") from None
        (plain if mime == "text/plain" else markup).append(value)
    text = "\n".join(plain).strip()[:24000]
    if text and not looks_like_html(text):
        return text, "plain"
    if text:
        # Some senders put a whole web page in the "plain text" part: read it as HTML.
        markup.insert(0, text)
    parser = _VisibleHTML()
    parser.feed("\n".join(markup)[:48000])
    text = "".join(parser.chunks).strip()[:24000]
    return text, "html" if text else "none"
