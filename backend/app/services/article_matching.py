"""Suggest catalog articles (negotiated prices) for a request's order lines.

The catalog lists the same product from several suppliers at different
negotiated prices, so the AI only matches order lines to distinct catalog
descriptions; the suppliers and prices come from the database.
"""
import logging
import uuid
from collections import defaultdict

from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models import Article

logger = logging.getLogger(__name__)

# Suppliers shown per order line.
MAX_SUGGESTIONS = 3

MATCH_PROMPT = """You match procurement order lines to a company's catalog of articles with
negotiated prices.

For each order line, return the numbers of the catalog items that are the same kind of product
or a direct substitute for it (e.g. "13-inch MacBook Air" -> a business laptop). Return an empty
list when nothing in the catalog fits; never force a match. At most 2 catalog items per line,
closest match first.

Never match shipping, freight, packing, delivery or installation lines: those charges belong to
the vendor's own offer and can't be bought separately from another supplier.

Catalog (number: description):
{catalog}"""


class _LineMatch(BaseModel):
    line: int
    catalog_items: list[int]


class _Matches(BaseModel):
    matches: list[_LineMatch]


def suggest_articles(
    db: Session, organization_id: uuid.UUID, descriptions: list[str]
) -> list[list[Article]]:
    """Per order line (same order as ``descriptions``), matching articles.

    Closest catalog match first; within one catalog item, cheapest supplier first.
    """
    suggestions: list[list[Article]] = [[] for _ in descriptions]
    if not settings.openai_api_key or not any(d.strip() for d in descriptions):
        return suggestions

    articles_by_description: dict[str, list[Article]] = defaultdict(list)
    for article in db.scalars(
        select(Article)
        .options(joinedload(Article.supplier))
        .where(Article.organization_id == organization_id)
    ):
        articles_by_description[article.description].append(article)
    catalog = sorted(articles_by_description)
    if not catalog:
        return suggestions

    try:
        parsed = OpenAI(api_key=settings.openai_api_key).chat.completions.parse(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": MATCH_PROMPT.format(
                        catalog="\n".join(f"{i}: {d}" for i, d in enumerate(catalog))
                    ),
                },
                {
                    "role": "user",
                    "content": "Order lines (number: description):\n"
                    + "\n".join(f"{i}: {d}" for i, d in enumerate(descriptions)),
                },
            ],
            response_format=_Matches,
            temperature=0,
        ).choices[0].message.parsed
    except Exception:  # suggestions are best-effort; the form works without them
        logger.exception("Error matching order lines to catalog articles")
        return suggestions

    for match in parsed.matches if parsed else []:
        if not 0 <= match.line < len(descriptions):
            continue
        for item in match.catalog_items:
            if 0 <= item < len(catalog):
                suppliers = articles_by_description[catalog[item]]
                suggestions[match.line].extend(sorted(suppliers, key=lambda a: a.unit_price))

    return [matches[:MAX_SUGGESTIONS] for matches in suggestions]
