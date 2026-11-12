# Parked

Things found while working on something else, judged real but out of scope
at the time. Each says what was observed and why it was left, so picking one
up does not mean rediscovering it.

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

## Route REITs to the dividend discount model

Realty Income and Prologis are refused today, and correctly: once their
real property spending was read, an unlevered free cash flow model shows
them never generating cash, because their growth is funded externally. But
a REIT must distribute most of its taxable income to keep its status, which
makes its dividend about as close to a contractual cash flow as equity
offers. The DDM path already exists for banks; pointing REITs at it would
turn two refusals into figures.

## Analyst estimates, for a second version

Every forecast in the model is derived from the filings, which is the
point, but a reader comparing it against the street has to go elsewhere.
Alpha Vantage's free tier (25 calls a day) is a candidate source. Check the
licensing before displaying any of it: redistributing someone else's
estimates is a different question from consuming them.

## Play Store release

Needs a Google Play Developer account ($25, one-off), an App Bundle (.aab)
rather than the APK built today, the data-safety form completed, and a
privacy policy at a public URL. None of it is code; all of it gates
release.

## Monetization, after the Play Store release

Free tier limited in some way - the Excel export, or roughly five
valuations a week - with the rest paid. Deliberately sequenced after the
store release rather than before it, so the first public version is not
also the first paid one.

## An always-on server

Render's free tier sleeps after fifteen minutes, so the first request after
a quiet spell pays a cold start of several seconds, which reads as the app
being broken. Starter is $7 a month. Worth settling before anyone is
invited to use it.

## Captive finance arms distort the bridge

Deere and companies like it run a lending business inside the group. Its
borrowings are inventory for that business rather than leverage on the
industrial one, so debt, WACC weights and free cash flow all read wrong
when the two are consolidated. Decide between refusing them, as banks are
refused, and separating the finance arm. They are valued today with no
special handling.

## Warnings should not be drowned out by the headline

The model says plainly when it is unsure, and the figure is rendered large
and bold directly above it. Check, before any public release, that someone
scanning the screen sees the caveat and not only the number.

One case deserves its own flag: a terminal value above about 100% of
enterprise value. Tesla is at 109%, which means the explicitly projected
years contribute negative value and the entire figure rests on the terminal
assumption - the one part of the model nobody can check. It is not an error
and the number is computed correctly, but a reader is entitled to be told
that the forecast they can see is not what produced the answer.

## Cosmetic copy typos

Small wording and punctuation errors across the app, noticed in passing and
not collected. Worth one pass with fresh eyes before the store listing.

## Refresh the long-run growth ceiling quarterly

`LONG_RUN_NOMINAL_GROWTH` and `LONG_RUN_GROWTH_AS_OF` in `assumptions.py`
hold 4.0% as of 16 September 2026 - the FOMC's longer-run medians for real
GDP (2.0%) and PCE inflation (2.0%), from the Summary of Economic
Projections. The SEP is published quarterly and the figure is flagged stale
after a hundred days, so the current one starts warning around **25
December 2026**. The **next SEP is December 2026**. Updating it is two lines
and a before/after; it is the ceiling on terminal growth for most
companies, so it moves every valuation.

## Refresh the equity risk premium monthly

`DAMODARAN_IMPLIED_ERP` and `DAMODARAN_ERP_AS_OF` in `assumptions.py` are
configured rather than fetched, deliberately. Damodaran republishes at the
start of each month; the figure is flagged stale after sixty days, so the
current one starts warning around **31 December 2026**. Updating it is two
lines and a before/after.
