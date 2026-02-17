"""ISW HTML report parser.

Extracts structured data from ISW (Institute for the Study of War)
daily assessment HTML pages into a JSON-serializable dict.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup, Tag


@dataclass
class Topline:
    headline: str
    body: str


@dataclass
class Section:
    id: str
    title: str
    body: str
    map_url: str | None = None
    map_title: str | None = None


@dataclass
class ParsedReport:
    date: str
    title: str
    toplines: list[Topline] = field(default_factory=list)
    key_takeaways: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    overview_map_url: str | None = None
    source_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_date_from_title(title: str) -> str:
    """Extract date string from a title like
    'Russian Offensive Campaign Assessment, February 16, 2026'.

    Returns ISO-format date string (YYYY-MM-DD).
    """
    match = re.search(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{1,2}),\s+(\d{4})",
        title,
    )
    if not match:
        msg = f"Could not extract date from title: {title}"
        raise ValueError(msg)

    month_str, day_str, year_str = match.groups()
    months = {
        "January": 1,
        "February": 2,
        "March": 3,
        "April": 4,
        "May": 5,
        "June": 6,
        "July": 7,
        "August": 8,
        "September": 9,
        "October": 10,
        "November": 11,
        "December": 12,
    }
    month = months[month_str]
    day = int(day_str)
    year = int(year_str)
    return f"{year:04d}-{month:02d}-{day:02d}"


def _clean_text(text: str) -> str:
    """Normalise whitespace in extracted text."""
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _extract_toplines(toplines_div: Tag) -> list[Topline]:
    """Extract topline paragraphs from the #toplines div.

    Each topline has a bold lead (headline) followed by body text.
    """
    toplines: list[Topline] = []

    for p in toplines_div.find_all("p", recursive=False):
        text = _clean_text(p.get_text())
        if not text:
            continue

        # Try to split on bold lead text
        strong_tags = p.find_all("strong", recursive=True)
        if strong_tags:
            headline_parts = []
            for s in strong_tags:
                headline_parts.append(_clean_text(s.get_text()))
            headline = " ".join(headline_parts)

            # Body = full text minus headline portion
            full_text = _clean_text(p.get_text())
            body = full_text
            # Try to find where headline ends in the full text
            if headline and full_text.startswith(headline):
                body = full_text[len(headline) :].strip()
            elif headline:
                # Headline might be slightly different due to whitespace
                body = full_text

            toplines.append(Topline(headline=headline, body=body))
        else:
            # No bold lead — treat entire paragraph as body with empty headline
            toplines.append(Topline(headline="", body=text))

    return toplines


def _extract_key_takeaways(takeaways_div: Tag) -> list[str]:
    """Extract ordered list of key takeaways."""
    takeaways: list[str] = []
    ol = takeaways_div.find("ol")
    if ol:
        for li in ol.find_all("li"):
            text = _clean_text(li.get_text())
            if text:
                takeaways.append(text)
    return takeaways


def _extract_map_from_block(map_block: Tag) -> tuple[str | None, str | None]:
    """Extract map image URL and title from a conflict-map-block div."""
    map_url: str | None = None
    map_title: str | None = None

    # Map image URL
    img = map_block.find("img")
    if img:
        map_url = img.get("src")

    # Map title — look for a sibling conflict-map-title div
    title_div = map_block.find_next_sibling("div", class_="conflict-map-title")
    if title_div:
        map_title = title_div.get("data-map-title")

    return map_url, map_title


def _extract_section_body(section_div: Tag) -> str:
    """Extract the text body of a section, excluding maps and sub-headings."""
    paragraphs: list[str] = []
    for p in section_div.find_all("p"):
        # Skip empty paragraphs
        text = _clean_text(p.get_text())
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _extract_sections(soup: BeautifulSoup) -> list[Section]:
    """Extract battlefield-direction sections from the report.

    Sections are identified by divs with a data-id attribute that are NOT
    'toplines', 'key-takeaways', or 'endnotes'.
    """
    skip_ids = {"toplines", "key-takeaways", "endnotes"}
    sections: list[Section] = []

    for div in soup.find_all(attrs={"data-id": True}):
        section_id = div["data-id"]
        if section_id in skip_ids:
            continue

        # Section title from the 'title' attribute or first h2
        section_title = div.get("title", "")
        if not section_title:
            h2 = div.find("h2")
            if h2:
                section_title = _clean_text(h2.get_text())

        # Maps within this section
        map_url: str | None = None
        map_title: str | None = None
        map_blocks = div.find_all("div", class_="conflict-map-block")
        if map_blocks:
            # Use the first map block for the section's primary map
            map_url, map_title = _extract_map_from_block(map_blocks[0])

        body = _extract_section_body(div)

        sections.append(
            Section(
                id=section_id,
                title=section_title,
                body=body,
                map_url=map_url,
                map_title=map_title,
            )
        )

    return sections


def _extract_overview_map(soup: BeautifulSoup) -> str | None:
    """Extract the overview (country-wide) map URL.

    The overview map is typically the first conflict-map-block in the
    'ukr-ops' section, showing the full Russo-Ukrainian war map.
    """
    ukr_ops = soup.find(attrs={"data-id": "ukr-ops"})
    if ukr_ops:
        first_map = ukr_ops.find("div", class_="conflict-map-block")
        if first_map:
            img = first_map.find("img")
            if img:
                return img.get("src")
    return None


def _extract_source_refs(soup: BeautifulSoup) -> list[str]:
    """Extract footnote source references from the endnotes section."""
    refs: list[str] = []
    endnotes = soup.find(attrs={"data-id": "endnotes"})
    if not endnotes:
        return refs

    for p in endnotes.find_all("p"):
        text = p.get_text()
        # Extract URLs from endnote text — handle ISW's 'dot' obfuscation
        # e.g. "https://tass dot ru/politika/26459009"
        urls = re.findall(r"https?://[^\s,;]+", text)
        for url in urls:
            # De-obfuscate ' dot ' → '.'
            clean_url = url.replace(" dot ", ".")
            refs.append(clean_url)

    return refs


def parse_report(html: str) -> ParsedReport:
    """Parse an ISW HTML report into structured data.

    Args:
        html: Raw HTML string of the ISW report page.

    Returns:
        ParsedReport with extracted fields.
    """
    soup = BeautifulSoup(html, "lxml")

    # Title
    title_el = soup.find("h1")
    title_text = _clean_text(title_el.get_text()) if title_el else ""

    # Date from title
    report_date = _extract_date_from_title(title_text)

    # Toplines
    toplines_div = soup.find(attrs={"data-id": "toplines"})
    toplines = _extract_toplines(toplines_div) if toplines_div else []

    # Key takeaways
    takeaways_div = soup.find(attrs={"data-id": "key-takeaways"})
    key_takeaways = _extract_key_takeaways(takeaways_div) if takeaways_div else []

    # Sections
    sections = _extract_sections(soup)

    # Overview map
    overview_map_url = _extract_overview_map(soup)

    # Source references
    source_refs = _extract_source_refs(soup)

    return ParsedReport(
        date=report_date,
        title=title_text,
        toplines=toplines,
        key_takeaways=key_takeaways,
        sections=sections,
        overview_map_url=overview_map_url,
        source_refs=source_refs,
    )
