import logging
from dataclasses import dataclass

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class ChapterContent:
    text: str
    paragraphs: list[dict]    # [{"id": str, "text": str, "html": str}]
    images: list[str]


def extract_chapter(html: str) -> ChapterContent:
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one(".page-container") or soup.body
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
        paragraphs.append({
            "id": pid,
            "text": para.get_text(" ", strip=True),
            "html": para.decode_contents(),
        })
    text = "\n\n".join(p["text"] for p in paragraphs if p["text"])
    return ChapterContent(text=text, paragraphs=paragraphs, images=images)
