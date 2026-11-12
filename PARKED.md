# Parked

Things found while working on something else, judged real but out of scope
at the time. Each says what was observed and why it was left, so picking one
up does not mean rediscovering it.

## Terminal growth sits too close to WACC for low-WACC companies

The low-beta names come out with a WACC only 1.7-2 percentage points above
their terminal growth, which makes the Gordon perpetuity enormous and the
whole valuation hostage to the gap. Measured 12 November 2026:

| | WACC | terminal growth | spread |
| --- | --- | --- | --- |
| XOM | 6.67% | 4.98% | 1.69pp |
| JNJ | 6.66% | 4.98% | 1.68pp |
| KO | 6.73% | 4.98% | 1.75pp |
| PG | 6.98% | 4.98% | 2.00pp |

The 1.5pp ceiling holds in every case, so nothing is broken - but a ceiling
that binds is not the same as a spread that is sensible, and these clear it
by a quarter of a point. Terminal growth defaults to the risk-free rate,
which at 4.98% is high against any plausible long-run real growth plus
inflation.

Worth investigating: a minimum spread wider than 1.5pp, a long-run growth
default below the risk-free rate, or both. Both change every valuation, so
it needs the same before/after treatment the beta and ERP change got.

## The web security headers have never applied

`render.yaml` declares them, but the static site is not Blueprint-managed,
so Render has never read that file for it. The headers the API sets are
unaffected - this is the PWA only. Needs a change in the Render dashboard
rather than in this repo.

## /diagnostics/upstream cannot see the shape of what Yahoo returns

`DIAGNOSTICS_TOKEN` *is* set on Render (confirmed 11 November 2026 - an
earlier note here saying otherwise was wrong). The endpoint reports status
codes per upstream endpoint, which is how we know the crumb mint is rate
limited there (HTTP 429) and `quoteSummary` is refused (HTTP 401) while this
machine gets both.

What it cannot show is which rows a statement frame actually contains, so
the missing `Tax Provision` row that cost Amazon 70% of its value had to be
inferred from a local/production diff rather than observed. Dumping
`income_stmt.index` would turn the next one of those from detective work
into a lookup.
