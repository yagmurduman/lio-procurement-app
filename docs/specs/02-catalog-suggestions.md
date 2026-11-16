# Spec 02 — Suggest negotiated catalog prices

> "People raise requests and go off buying whatever they find. We've negotiated
> specific prices for specific articles with our suppliers. When someone creates
> a request, I want the system to suggest the matching articles we already have
> on catalog, so we actually use those agreements." — Acme Corp

Acme's own sample quotes show the problem: the Hollis moss panel was quoted at
$1,560, while the catalog has a framed moss panel with logo at $320.

## Approach

- **Suggestions while creating a request.** After a PDF upload (automatically)
  or via **Check catalog prices** (manual lines), each order line shows up to 3
  matching catalog articles with supplier, negotiated price and the saving per
  unit (only when the units are comparable).
- **Use this** replaces the line with the catalog article at its negotiated
  price and sets the request's vendor to that supplier — the price only holds
  with them.
- **AI matching, database prices.** The AI only decides which catalog items
  match a line (it understands "MacBook Air" ≈ "Business laptop"). Suppliers and
  prices come from the database, so they can't be made up.
- **Ordering:** closest match first; within one article, cheapest supplier
  first. The same article is often on catalog from several suppliers (e.g. a
  business laptop from $1,130 to $2,039).
- Only the user's own organization's catalog is used.
- **No suggestions for shipping, delivery or installation lines.** Those charges
  belong to the vendor's offer (e.g. freight for this exact moss panel) and
  can't be bought separately; suggesting "parcel shipping" was just noise.

## Decisions & trade-offs

- **gpt-4o for matching.** gpt-4o-mini missed the moss panels depending on
  prompt wording; gpt-4o matched them consistently. ~1.5 s and ~2 cents per
  check.
- **Suggestions, not enforcement.** The requester decides; nothing is blocked.

## Verify

1. `docker compose up --build`
2. Sign in as `requester@acme.com` → **New Request** → upload `QuoteA0492_23.Pdf`.
3. Under the moss panel line ($1,560): *Framed preserved moss panel with company
   logo cut-out · Redwood Facility Group · $320 / piece*. **Use this** sets the
   line to $320 and the vendor to Redwood Facility Group.
4. Add a line "HP toner cartridge black" → **Check catalog prices** → three
   suppliers, cheapest first.

## Limitations / with more time

- One vendor per request: picking articles from different suppliers leaves the
  last one as vendor. Splitting into one request per supplier would fix it.
- Show buyers on approval whether a cheaper catalog article was ignored.
- Link the order line to the article number instead of copying its fields.
- Ranking can shift with the other lines in the request (e.g. which moss article
  comes first); all matches are still shown.
