"""Per-organization booking rules (see ``BookingRule``).

Applied deterministically on top of the AI's commodity classification: a
customer's accounting rules are non-negotiable, so they can't depend on the
model following an instruction.
"""
import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models import Organization
from app.schemas.organizations import BookingRule, OrganizationSettings


def org_settings(db: Session, organization_id: uuid.UUID) -> OrganizationSettings:
    org = db.get(Organization, organization_id)
    return OrganizationSettings(**(org.settings or {})) if org else OrganizationSettings()


def find_booking_rule(
    rules: list[BookingRule],
    title: str,
    line_descriptions: Iterable[str],
    total: float | None,
) -> BookingRule | None:
    """The first rule whose keywords appear in the title or an order line.

    Rules with ``max_total`` only match requests below that amount.
    """
    haystack = " ".join([title, *line_descriptions]).lower()
    for rule in rules:
        if rule.max_total is not None and (total is None or total >= rule.max_total):
            continue
        if any(keyword.lower() in haystack for keyword in rule.keywords):
            return rule
    return None
