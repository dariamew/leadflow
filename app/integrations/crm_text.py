"""Convert Bitrix24 rich text to the plain text used for facts and citations."""

import re
from html.parser import HTMLParser

BLOCKS = {
    "p",
    "div",
    "blockquote",
    "pre",
    "li",
    "ul",
    "ol",
    "table",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "hr",
}
INLINE = {"a", "b", "strong", "i", "em", "u", "s", "strike", "span", "font", "code", "small", "img"}
BBCODE = re.compile(
    r"\[\s*(/?)\s*(p|div|br|b|strong|i|em|u|s|strike|font|size|color|url|email|"
    r"img|quote|code|list|li|\*|table|tr|td|th|left|right|center|justify|spoiler)"
    r"(?:\s*=\s*[^\]\r\n]*)?\s*\]",
    re.IGNORECASE,
)


class PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in BLOCKS or tag == "br":
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")
        elif tag not in INLINE:
            # Keep domain-specific notation such as <CRM> as literal text.
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in BLOCKS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")
        elif tag not in INLINE and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def crm_plain_text(value: str) -> str:
    def replace_tag(match):
        tag = match[2].lower()
        if tag in BLOCKS or tag in {
            "br",
            "quote",
            "list",
            "*",
            "left",
            "right",
            "center",
            "justify",
        }:
            return "\n"
        if tag in {"td", "th"}:
            return " "
        return ""

    parser = PlainTextParser()
    parser.feed(BBCODE.sub(replace_tag, value))
    parser.close()
    text = "".join(parser.parts).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ")
    text = "\n".join(re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()
