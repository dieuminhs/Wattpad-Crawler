from dataclasses import dataclass


@dataclass(frozen=True)
class ExportPreset:
    body_font_size: str
    line_height: str
    paragraph_margin: str
    max_width: str
    page_margin: str


EXPORT_PRESET_STYLES = {
    "classic": ExportPreset(
        body_font_size="1rem",
        line_height="1.65",
        paragraph_margin="1em 0",
        max_width="42em",
        page_margin="2em auto",
    ),
    "cozy": ExportPreset(
        body_font_size="1.08rem",
        line_height="1.85",
        paragraph_margin="1.2em 0",
        max_width="44em",
        page_margin="2.5em auto",
    ),
    "compact": ExportPreset(
        body_font_size="0.95rem",
        line_height="1.45",
        paragraph_margin="0.7em 0",
        max_width="48em",
        page_margin="1.5em auto",
    ),
}


def export_preset_css(preset: str) -> str:
    style = EXPORT_PRESET_STYLES.get(preset, EXPORT_PRESET_STYLES["classic"])
    return "\n".join(
        [
            f":root{{--export-preset:{preset};}}",
            "body{font-family:Georgia,serif;"
            f"font-size:{style.body_font_size};"
            f"max-width:{style.max_width};"
            f"margin:{style.page_margin};"
            f"padding:0 1em;line-height:{style.line_height};}}",
            f"p{{margin:{style.paragraph_margin};}}",
            "h1,h2{font-family:system-ui,sans-serif;line-height:1.15;}",
            "hr{border:none;border-top:1px solid #ccc;margin:3em 0;}",
            "div.chapter{margin-bottom:4em;}",
        ]
    )
