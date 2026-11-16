# Spec 03 — Customer booking rules

> "For us, cable ties always have to be booked under Electrical Engineering,
> printer toner under IT Hardware, and small software subscriptions under Office
> Supplies. They're quirks on our side, but they're non-negotiable." — Acme Corp

## Approach

- **Rules are per organization**, stored in `OrganizationSettings` (the existing
pattern for tenant config). Other tenants are unaffected.
- **Applied in code, not by the AI.** A rule matches when one of its keywords
appears in the title or an order line (and, if set, the total is below its
limit). "Non-negotiable" can't depend on a model following an instruction.
- **Applied twice:**
  - on upload, so the form shows the right group, with a note naming the rule;
  - on save, so it holds even if someone picks another group by hand. The
  override is written to the request's activity log.

## Acme's rules (seeded)


| Rule                                | Keywords                                                            | Group                                  |
| ----------------------------------- | ------------------------------------------------------------------- | -------------------------------------- |
| Cable ties                          | cable tie, zip tie                                                  | Electrical Engineering (11)            |
| Printer toner                       | toner                                                               | Information Technology / Hardware (29) |
| Software subscriptions under $2,000 | software subscription, saas, software license, subscription license | Office Equipment (15)                  |


## Assumptions — to confirm with Acme

- "Office Supplies" doesn't exist in the taxonomy; mapped to Office Equipment.
- "Small" = under $2,000 (the same threshold Acme uses for approvals).

## Also fixed

Saving one organization setting (e.g. required fields) replaced the whole
settings object, which would have wiped the booking rules. Settings are now
merged on save, and rules pointing to an unknown group are rejected.

## Verify

1. `docker compose up --build`
2. Sign in as `requester@acme.com` → **New Request**, add an order line
 "cable ties", pick any commodity group, submit.
3. The saved request is under Electrical Engineering; its activity log says why.
4. The same request in another tenant (e.g. `admin@walmart.com`) keeps its group.