import logging
import re

from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import CommodityGroup
from app.schemas.extraction import ExtractedVendorData, ExtractionResponse, OrderLineData

logger = logging.getLogger(__name__)

# Relative tolerance for "these numbers agree" checks (rounding on quotes).
_TOLERANCE = 0.01


# --- LLM-facing schemas ------------------------------------------------------
# Structured outputs (strict mode) need every key present; fields the document
# may lack are nullable so "not on the document" is a valid answer.


class _QuoteLine(BaseModel):
    description: str
    quantity: float
    unit: str
    unit_price: float
    total_price: float


class _Quote(BaseModel):
    title: str
    # Filled before vendor_name so the model states its evidence first. Needed
    # even with gpt-4o: without it the verdeform quote's vendor came back as
    # the addressee.
    vendor_evidence: str
    vendor_name: str | None
    vendor_tax_id: str | None
    customer_or_department: str | None
    line_items: list[_QuoteLine]
    tax_and_fees: float | None
    grand_total: float | None


class _Classification(BaseModel):
    commodity_group_id: int | None


EXTRACTION_PROMPT = """You extract structured data from US vendor quotes (offers) sent TO the buying company.

Rules:
- vendor_name: the company ISSUING the quote (the seller). It is usually in the letterhead,
  footer, signature or "Prepared by" block. NEVER the recipient / "Prepared for" / addressee.
  If the letterhead is an image (no name in the text), use other clues: "thank you for your
  inquiry at X", the sales rep's email domain, or whose terms and conditions apply.
- vendor_evidence: one sentence quoting where in the text the seller's name appears.
- vendor_tax_id: the SELLER's EIN / Federal Tax ID / TIN, formatted as on the document
  (e.g. "94-1985704"). null if the document does not state one. Never invent one.
- customer_or_department: the recipient department or company the quote is addressed to.
- title: 2-5 word summary of what is being bought.
- line_items: only positions that are actually part of the offered total.
  * EXCLUDE positions marked as "Alternative", "Alt.", "optional", or priced "U.P." (unit
    price only) — they are not included in the subtotal.
  * EXCLUDE heading/group rows that carry no price (e.g. "1. Office furniture").
  * Do not confuse item/position numbers or article numbers with quantities.
  * If a line has a discount or surcharge, unit_price is the NET unit price after it,
    so that quantity * unit_price == total_price.
  * Shipping, freight, packing, delivery and installation charges are ALWAYS line items,
    also when they appear only in the totals block (e.g. "Shipping (net) $215.00" ->
    a line "Shipping", quantity 1, unit "lump sum").
  * Taxes and government fees (sales tax, recycling fees) are NEVER line items.
  * unit: write abbreviations out ("ea." -> "pieces", "sq ft" -> "square feet"); use
    "lump sum" for services and charges priced as a single amount.
- tax_and_fees: the sum of all taxes and government fees in the totals block (e.g. sales
  tax on items + sales tax on shipping + recycling fee). 0 if there are none.
- grand_total: the final total including everything, as printed. null if absent.
- Numbers are plain decimals in US format ("1,438.00" -> 1438.00), no currency symbols."""

CLASSIFICATION_PROMPT = """Classify a procurement request into exactly one commodity group.
Pick the group that best describes the PRIMARY item(s) being purchased.
- Branded office decor and furnishings (e.g. moss wall art, logo panels) -> Office Equipment.
- Delivery/installation that comes with goods does not change the goods' group.
Return null only if nothing fits at all.

Commodity groups (ID | Category | Name):
{groups}"""


# --- helpers -----------------------------------------------------------------


def _normalize_tax_id(value: str | None) -> str:
    """US EINs are 9 digits written XX-XXXXXXX; quotes print them many ways."""
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 9:
        return f"{digits[:2]}-{digits[2:]}"
    return value.strip()


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(_TOLERANCE * max(abs(a), abs(b)), 0.02)


def _arithmetic_issues(quote: _Quote) -> list[str]:
    """Deterministic checks the model's output must satisfy."""
    issues: list[str] = []
    for line in quote.line_items:
        expected = line.quantity * line.unit_price
        if not _close(expected, line.total_price):
            issues.append(
                f'Line "{line.description[:40]}": {line.quantity:g} x '
                f"{line.unit_price:.2f} = {expected:.2f}, but total is "
                f"{line.total_price:.2f}."
            )
    # Lines carry everything except taxes/fees, so they must reconcile with
    # the printed grand total.
    lines_sum = sum(line.total_price for line in quote.line_items)
    if quote.grand_total is not None and quote.tax_and_fees is not None:
        if not _close(lines_sum + quote.tax_and_fees, quote.grand_total):
            issues.append(
                f"Line totals ({lines_sum:.2f}) plus taxes/fees "
                f"({quote.tax_and_fees:.2f}) = {lines_sum + quote.tax_and_fees:.2f}, "
                f"but the quote's grand total is {quote.grand_total:.2f}."
            )
    elif quote.grand_total is not None and lines_sum > quote.grand_total * (1 + _TOLERANCE):
        issues.append(
            f"Line totals sum to {lines_sum:.2f}, more than the grand total "
            f"{quote.grand_total:.2f}."
        )
    return issues


def _extract(client: OpenAI, pdf_text: str) -> tuple[_Quote, list[str]]:
    """Extract, verify the arithmetic, and retry once with feedback if needed."""
    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": f"Quote text:\n\n{pdf_text}"},
    ]

    def call():
        message = client.chat.completions.parse(
            model=settings.openai_model,
            messages=messages,
            response_format=_Quote,
            temperature=0,
        ).choices[0].message
        if message.parsed is None:
            raise ValueError(message.refusal or "The AI returned no data")
        return message

    message = call()
    quote = message.parsed
    issues = _arithmetic_issues(quote)
    if issues:
        logger.info("Extraction failed arithmetic checks, retrying: %s", issues)
        messages += [
            {"role": "assistant", "content": message.content or ""},
            {
                "role": "user",
                "content": "Your extraction does not reconcile with the document:\n- "
                + "\n- ".join(issues)
                + "\nRe-read the quote (alternatives, discounts, item numbers vs. "
                "quantities, shipping as a line, taxes not as a line) and return the "
                "corrected extraction.",
            },
        ]
        quote = call().parsed
        issues = _arithmetic_issues(quote)
    return quote, issues


def _classify(
    client: OpenAI, quote: _Quote, groups: list[CommodityGroup]
) -> CommodityGroup | None:
    """Two-stage classification: a base group first, then refine within its category.

    The taxonomy has ~2,000 groups; most are generated variants of ~390 base
    groups ("Toner Cartridges — Bulk / Pallet / ..."). Stage 1 picks among the
    base groups, stage 2 among the ≤100 groups of that base group's category.
    Choosing among bare category names instead loses too much context (a
    grinding machine landed in "Maintenance, Repair & Operations").
    """
    items = "\n".join(f"- {line.description}" for line in quote.line_items)
    request_summary = f"Title: {quote.title}\nVendor: {quote.vendor_name}\nItems:\n{items}"

    def pick(options: list[CommodityGroup]) -> CommodityGroup | None:
        by_id = {g.id: g for g in options}
        parsed = client.chat.completions.parse(
            model=settings.openai_classification_model,
            messages=[
                {
                    "role": "system",
                    "content": CLASSIFICATION_PROMPT.format(
                        groups="\n".join(f"{g.id} | {g.category} | {g.name}" for g in options)
                    ),
                },
                {"role": "user", "content": request_summary},
            ],
            response_format=_Classification,
            temperature=0,
        ).choices[0].message.parsed
        if parsed is None or parsed.commodity_group_id is None:
            return None
        group = by_id.get(parsed.commodity_group_id)
        if group is None:
            logger.warning("Invalid commodity group ID returned: %s", parsed.commodity_group_id)
        return group

    # Variants are named "<base> — <variant>" by scripts/generate_commodity_groups.py.
    first = pick([g for g in groups if " — " not in g.name])
    if first is None:
        return None
    return pick([g for g in groups if g.category == first.category]) or first


# --- entry point -------------------------------------------------------------


def extract_vendor_data(pdf_text: str, db: Session) -> ExtractionResponse:
    """Extract vendor data from quote text and classify its commodity group."""
    if not pdf_text or not pdf_text.strip():
        return ExtractionResponse(success=False, error="No text provided for extraction")

    if not settings.openai_api_key:
        return ExtractionResponse(
            success=False,
            error="OPENAI_API_KEY is not configured on the server",
        )

    groups = list(db.scalars(select(CommodityGroup).order_by(CommodityGroup.id)))
    if not groups:
        return ExtractionResponse(
            success=False,
            error="Failed to load commodity groups for classification",
        )

    client = OpenAI(api_key=settings.openai_api_key)
    try:
        quote, issues = _extract(client, pdf_text)
    except Exception:
        logger.exception("Error extracting vendor data")
        return ExtractionResponse(
            success=False,
            error="We couldn't read this quote automatically. Please fill in the "
            "details manually.",
        )

    try:
        group = _classify(client, quote, groups)
    except Exception:
        logger.exception("Error classifying commodity group")
        group = None

    extracted = ExtractedVendorData(
        title=quote.title,
        vendor_name=quote.vendor_name or "",
        vat_id=_normalize_tax_id(quote.vendor_tax_id),
        department=quote.customer_or_department or "",
        order_lines=[
            OrderLineData(
                position_description=line.description,
                unit_price=line.unit_price,
                amount=line.quantity,
                unit=line.unit,
                total_price=line.total_price,
            )
            for line in quote.line_items
        ],
        total_cost=quote.grand_total
        or sum(line.total_price for line in quote.line_items),
        commodity_group_id=group.id if group else None,
        commodity_group_name=group.name if group else None,
    )

    missing_fields: list[str] = []
    if not extracted.title.strip():
        missing_fields.append("Title/Short Description")
    if not extracted.vendor_name.strip():
        missing_fields.append("Vendor Name")
    if not extracted.vat_id.strip():
        missing_fields.append("Tax ID")
    if not extracted.department.strip():
        missing_fields.append("Department")
    if not extracted.order_lines:
        missing_fields.append("Order Lines")
    if not extracted.total_cost:
        missing_fields.append("Total Cost")
    if extracted.commodity_group_id is None:
        missing_fields.append("Commodity Group")

    return ExtractionResponse(
        success=True,
        data=extracted,
        missing_fields=missing_fields or None,
        warnings=issues or None,
    )
