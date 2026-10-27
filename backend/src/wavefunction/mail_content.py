"""Readable email content without MIME parser bookkeeping or HTML presentation markup."""

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser


class _MailText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in {"head", "style", "script"}:
            self.skip += 1
        if self.skip:
            return
        if tag in {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")
        if tag in {"td", "th"}:
            self.parts.append(" ")
        attrs = dict(attrs)
        if tag == "a":
            href = attrs.get("href") or ""
            self.links.append((href, len(self.parts)))
        if tag == "img" and attrs.get("alt"):
            self.parts.append(attrs["alt"])

    def handle_endtag(self, tag):
        if tag in {"head", "style", "script"} and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "a" and self.links:
            href, start = self.links.pop()
            if (
                href.lower().startswith(("http://", "https://", "mailto:"))
                and href != "".join(self.parts[start:]).strip()
            ):
                self.parts.append(f" ({href})")
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_mail_text(html: str) -> str:
    """Keep visible text and link destinations; never fetch images or other resources."""
    parser = _MailText()
    parser.feed(html)
    return "\n".join(line for raw in "".join(parser.parts).splitlines() if (line := " ".join(raw.split())))


def _headers(part):
    # Standard names are strings; extension headers use an {"Other": name} enum variant.
    return {
        header["name"]: header["value"]
        for header in part.get("headers", [])
        if isinstance(header["name"], str)
    }


def _header_value(value):
    if "Text" in value:
        return value["Text"]
    if "Address" in value:
        address = value["Address"]
        entries = address.get("List", [])
        if "Group" in address:
            entries = [entry for group in address["Group"] for entry in group.get("addresses", [])]
        return [{"name": entry.get("name"), "address": entry.get("address")} for entry in entries]
    if "DateTime" in value:
        date = value["DateTime"]
        offset = timedelta(hours=date["tz_hour"], minutes=date["tz_minute"])
        if date["tz_before_gmt"]:
            offset = -offset
        try:
            return datetime(
                *(date[key] for key in ("year", "month", "day", "hour", "minute", "second")),
                tzinfo=timezone(offset),
            ).isoformat()
        except ValueError:
            return None
    return None


def readable_himalaya_message(message: dict) -> dict:
    """Decode Himalaya 2.x's mail-parser object using its body/attachment part indexes."""
    if not isinstance(message, dict) or not message.get("parts"):
        raise ValueError("Himalaya returned an unreadable message instead of parsed email content.")
    parts = message["parts"]
    headers = _headers(parts[0])
    result = {
        name: value
        for name in ("subject", "from", "to", "cc", "reply_to", "date")
        if (value := _header_value(headers.get(name, {}))) is not None
    }
    if value := _header_value(headers.get("message_id", {})):
        result["internet_message_id"] = value

    def body_text(indexes):
        chunks = []
        for index in indexes:
            body = parts[index]["body"]
            text = body.get("Text")
            if text is None and "Html" in body:
                text = html_mail_text(body["Html"])
            if text and text.strip():
                chunks.append(text.strip())
        return "\n\n".join(chunks)

    result["body"] = body_text(message.get("text_body", [])) or body_text(message.get("html_body", []))
    result["attachments"] = []
    for index in message.get("attachments", []):
        part = parts[index]
        headers = _headers(part)
        content_type = headers.get("content_type", {}).get("ContentType", {})
        disposition = headers.get("content_disposition", {}).get("ContentType", {})
        attrs = {attr["name"]: attr["value"] for attr in (content_type.get("attributes") or [])}
        attrs.update({attr["name"]: attr["value"] for attr in (disposition.get("attributes") or [])})
        attachment = {
            "part_id": index,
            "filename": attrs.get("filename") or attrs.get("name"),
            "content_type": "/".join(
                filter(None, [content_type.get("c_type"), content_type.get("c_subtype")])
            ),
        }
        body = part["body"]
        for kind in ("Binary", "InlineBinary"):
            if kind in body:
                attachment["size_bytes"] = len(body[kind])
        result["attachments"].append(attachment)
    return result
