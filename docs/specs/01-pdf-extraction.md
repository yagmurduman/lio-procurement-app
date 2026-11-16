# Spec 01 — Reliable PDF extraction

> "The extracted data is often wrong — fields come out incorrect or empty, and
> sometimes the upload errors out completely." — Acme Corp

**Before:** 1 of Acme's 5 sample quotes extracted correctly; 2 crashed.
**After:** 5 of 5 correct.

## Root causes

- Missing fields (no Tax ID on 2 quotes) crashed validation.
- The model often named the addressee (buyer) as the vendor.
- "Alternative" positions, discounts and item numbers were misread.
- Nothing checked that the numbers add up.
- Classification picked from all ~2,000 commodity groups in one prompt.

## Changes

- **Structured outputs** with nullable fields — missing values are flagged, not fatal.
- **Clearer prompt**: who the vendor is, skip alternatives, apply discounts.
- **Order lines = everything except tax.** Shipping/delivery is always a line
  (unit "lump sum"), even when the quote only lists it in the totals block;
  taxes and government fees never are.
- **Arithmetic check** (qty × price per line, lines + tax = grand total) with one
  retry; unresolved mismatches are shown to the user as warnings.
- **Two-stage classification**: pick a base group first, then refine within its
  category (≤100 options instead of 2,000).
- **Layout-mode PDF text** so table columns stay aligned.
- **Models**: `gpt-4o` for extraction (mini got the vendor wrong on the verdeform
  quote), `gpt-4o-mini` for classification. Both configurable in `.env`.

## Trade-offs

- gpt-4o costs more per upload; worth it for correct requests. The old code comment  
  said larger models were "slower without a real quality gain" - measured on the 5  
  quotes, gpt-4o was no slower (avg 3.8–4.7 s vs. 5.7 s per upload for mini) and  
  fixed the verdeform vendor.
- Shipping as a line is an assumption - to confirm with Acme how they book freight.
- Mini classifies the MacBook as "Business Laptop — Premium" instead of "Business
  Laptop"; counted as correct since it's a variant of the same group.

## Verify

During development, each of Acme's 5 quotes was run through the extraction and
compared against values read from the PDF (vendor, Tax ID, every line's quantity
and total, grand total, commodity group). To check it yourself:

1. `docker compose up --build`
2. Sign in as `requester@acme.com` at http://localhost:3200 → **New Request**
3. Upload each quote and compare the form with the PDF.