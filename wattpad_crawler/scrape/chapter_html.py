import logging
from dataclasses import dataclass

import nh3
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Phase 1 SAN-01: paragraph HTML allowlist. Reading-rich tags (D-01),
# per-tag attributes (D-02), data-p-id allowed on every tag (D-02),
# class/style stripped from all tags (D-03). nh3's default url_schemes
# already excludes javascript:/data: so the D-02 "http/https only for
# <a href>" requirement is satisfied without a custom url_schemes set —
# invalid schemes drop the attribute, preserving link text.
_PARAGRAPH_CLEANER = nh3.Cleaner(
    tags={"img", "br", "b", "i", "em", "strong", "u", "a"},
    attributes={
        "img": {"src", "alt"},
        "a": {"href"},
        "*": {"data-p-id"},
    },
    strip_comments=True,
)


@dataclass
class ChapterContent:
    text: str
    paragraphs: list[dict]  # [{"id": str, "text": str, "html": str}]
    images: list[str]


def extract_chapter(html: str) -> ChapterContent:
    soup = BeautifulSoup(html, "lxml")
    container = soup.body or soup.select_one(".page-container")
    if container is None:
        return ChapterContent(text="", paragraphs=[], images=[])

    paragraphs: list[dict] = []
    images: list[str] = []

    # Match anything with a data-p-id attribute. Wattpad has used both <pre>
    # and <p> in different layouts; the attribute is the reliable selector.
    para_els = container.find_all(attrs={"data-p-id": True})

    if not para_els:
        logger.warning(
            "extract_chapter: no elements with data-p-id found; "
            "Wattpad HTML structure may have changed"
        )

    for para in para_els:
        pid = para.get("data-p-id", "")
        for img in para.find_all("img"):
            src = img.get("src", "")
            if src:
                images.append(src)
        raw_html = para.decode_contents()
        clean_html = _PARAGRAPH_CLEANER.clean(raw_html)  # SAN-01: D-04
        paragraphs.append(
            {
                "id": pid,
                "text": para.get_text(" ", strip=True),
                "html": clean_html,
            }
        )
    text = "\n\n".join(p["text"] for p in paragraphs if p["text"])
    return ChapterContent(text=text, paragraphs=paragraphs, images=images)
