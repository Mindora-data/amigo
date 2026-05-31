from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import html
import re
import xml.etree.ElementTree as ET
from urllib import request
from uuid import uuid4


@dataclass(slots=True)
class RssSource:
    source_id: str
    name: str
    url: str
    theme: str
    active: bool
    trust: float
    created_at: datetime
    updated_at: datetime
    last_import_at: datetime | None = None
    last_import_ok: bool | None = None
    last_import_error: str | None = None
    last_added_count: int = 0


@dataclass(slots=True)
class RssItem:
    item_id: str
    source_id: str
    title: str
    link: str
    published_at: datetime | None
    summary: str
    theme: str
    fetched_at: datetime


THEMES = {
    "cultura",
    "ciencia",
    "tecnologia",
    "filosofia",
    "libros",
    "cine",
    "comics",
    "noticias",
    "mercados",
    "inversion",
    "etf",
    "cripto",
    "otros",
}


DEFAULT_FINANCIAL_RSS_SOURCES = [
    {
        "name": "Invezz Stocks & Shares",
        "url": "https://invezz.com/news/stock-market/feed/",
        "theme": "mercados",
        "trust": 0.70,
    },
    {
        "name": "Invezz Economic News",
        "url": "https://invezz.com/news/economic/feed/",
        "theme": "mercados",
        "trust": 0.70,
    },
    {
        "name": "Invezz Market Analysis",
        "url": "https://invezz.com/news/trading-ideas/feed/",
        "theme": "inversion",
        "trust": 0.70,
    },
    {
        "name": "Invezz Alternative Investment",
        "url": "https://invezz.com/news/alternative-investment/feed/",
        "theme": "inversion",
        "trust": 0.68,
    },
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "theme": "cripto",
        "trust": 0.78,
    },
    {
        "name": "Invezz Cryptocurrency",
        "url": "https://invezz.com/news/cryptocurrency/feed/",
        "theme": "cripto",
        "trust": 0.70,
    },
    {
        "name": "ETF Trends",
        "url": "https://www.etftrends.com/feed/",
        "theme": "etf",
        "trust": 0.66,
    },
]


def _clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _parse_date(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(value.replace("GMT", "+0000"), fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(node):
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names:
            return _clean_text(child.text or "")
    return ""


def parse_rss_items(xml_text: str, *, source_id: str, theme: str, fetched_at: datetime) -> list[RssItem]:
    root = ET.fromstring(xml_text)
    nodes = [item for item in root.iter() if item.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    out: list[RssItem] = []
    for node in nodes[:50]:
        title = _child_text(node, ("title",))[:180]
        link = _child_text(node, ("link", "guid", "id"))[:500]
        if not link:
            for child in list(node):
                if child.tag.rsplit("}", 1)[-1].lower() == "link" and child.attrib.get("href"):
                    link = str(child.attrib["href"])[:500]
                    break
        summary = _child_text(node, ("description", "summary", "content", "content:encoded"))[:700]
        published = _parse_date(_child_text(node, ("pubdate", "published", "updated", "dc:date")))
        if not title:
            continue
        out.append(
            RssItem(
                item_id=f"rss-item::{uuid4()}",
                source_id=source_id,
                title=title,
                link=link,
                published_at=published,
                summary=summary,
                theme=theme,
                fetched_at=fetched_at,
            )
        )
    return out


def fetch_rss_xml(url: str, timeout: int = 20) -> str:
    req = request.Request(url, headers={"User-Agent": "amigo-local-rss/1.0"})
    with request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def source_id_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"rss::{slug or uuid4().hex[:10]}"


def normalize_theme(value: str) -> str:
    value = _clean_text(value).lower()
    return value if value in THEMES else "otros"
