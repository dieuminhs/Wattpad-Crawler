from dataclasses import dataclass

from bs4 import BeautifulSoup


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
    for pre in container.find_all("pre"):
        pid = pre.get("data-p-id", "")
        for img in pre.find_all("img"):
            src = img.get("src", "")
            if src:
                images.append(src)
        paragraphs.append({
            "id": pid,
            "text": pre.get_text(" ", strip=True),
            "html": pre.decode_contents(),
        })
    text = "\n\n".join(p["text"] for p in paragraphs if p["text"])
    return ChapterContent(text=text, paragraphs=paragraphs, images=images)
