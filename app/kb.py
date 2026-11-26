"""The sample help center: markdown files holding many short articles each (``# Title`` then ``<!-- id: slug -->``)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT

_ARTICLE = re.compile(r"^# (?P<title>.+?)\n<!-- id: (?P<slug>[a-z0-9-]+) -->\n(?P<body>.*?)(?=^# |\Z)", re.M | re.S)
MAX_WORDS = 160


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    category: str
    body: str

    def chunks(self) -> list[str]:
        """Whole paragraphs, merged up to MAX_WORDS. Articles are short, so most are a single chunk."""
        out: list[str] = []
        for para in (p.strip() for p in self.body.split("\n\n")):
            if not para:
                continue
            if out and len(out[-1].split()) + len(para.split()) <= MAX_WORDS:
                out[-1] += "\n\n" + para
            else:
                out.append(para)
        return out


def load_articles(directory: Path | None = None) -> list[Article]:
    directory = directory or ROOT / "helpcenter"
    articles: list[Article] = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        for m in _ARTICLE.finditer(text):
            articles.append(Article(m["slug"], m["title"].strip(), path.stem, m["body"].strip()))
    slugs = [a.slug for a in articles]
    if len(slugs) != len(set(slugs)):
        raise ValueError("duplicate article id in the help center")
    return articles
