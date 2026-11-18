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

## The caveat block does not use the width it has

At phone width the badge, the one-line qualifier, the split bar and the
callout stack, which is the right shape for a narrow column. At 1280px they
still stack, so a single sentence runs the full width of the window and the
split bar is a thin line stretched across it.

The design this was built from put the callout in a side column at wide
widths, with the figure and its qualifier on the left. Worth doing; it was
left out to keep the first version of the tiers to one shape.

## The honesty note still says WACC

Everything else a reader meets now says "the discount rate"; the honesty
note says "It assumes a WACC of 11.86%". Left alone deliberately - the
sentence is duplicated character for character in Dart so it can update
mid-drag, so changing it means editing both and regenerating the parity
fixtures, which is more than a word swap deserves on its own.

Consider "a discount rate (WACC) of X%" the next time that note is edited
in lockstep for some other reason.

## Typed-price variant rejected (November 2026)

Too much friction for users.

Investigated as a way to launch without licensing a price feed: the reader
types the market price, the model multiplies it by the share count from the
filings for market-cap weights, and beta comes bottom-up from Damodaran's
industry tables relevered on the company's own debt. It worked
analytically - the weights came out within a point of the market-based ones
(Apple 98.5% against a true 98.5%, Alphabet 96.7% against 98.7%) - and the
residual error was the beta alone, within +/-23% on seven of nine
companies.

It was rejected on the interaction, not the arithmetic: asking for a price
on every valuation is a question the reader came to the app to have
answered.

The consequence is that a licensed end-of-day price feed is now on the
critical path for any public release, rather than one option among several.

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

## The static risk-free fallback is 90bp stale and swings a valuation 18%

`FALLBACK_RISK_FREE_RATE = 0.0425` in `assumptions.py` against a live
ten-year par yield of **5.15%** (13 November 2026). The gap is not cosmetic:
it was found by accident during the Stage 0 before/after, when one run of
BABA came out at **$223.00** instead of **$189.31** - a **+17.8%** move -
because both rate lookups failed and the static default fired.

Stage 0 makes this rarer (two independent sources now, Treasury and the
aggregator, where there was one), and nothing about the fallback changed, so
it was left alone deliberately rather than quietly re-based mid-change.

Two things worth doing, both needing a decision:

1. **Re-base the constant**, with a before/after. It has no as-of date and
   no staleness flag, unlike the ERP and the long-run growth figure, which
   is why it drifted this far unnoticed.
2. **Say when it fires.** Today a valuation built on the static default is
   indistinguishable from one built on the live curve: the provenance
   records `"default"`, but nothing is promoted to a warning tier. An 18%
   swing in the headline figure arguably belongs in the caution tier.

## The app's honesty note drops the exchange-rate sentence when a slider moves

`analysis.honesty_note()` adds a sentence naming the conversion rate for a
company whose statements are in another currency. `buildHonestyNote()` in
`app/lib/dcf_engine.dart`, which rebuilds the note locally as a slider moves
so it does not have to be re-fetched mid-drag, has no equivalent branch.

So on TSM the sentence is there on first load and vanishes the moment the
reader touches a slider. Pre-existing, found while changing the sentence's
wording for Stage 0. The parity fixtures do not catch it because none of the
recorded cases is a converted company - which is the other half of the fix.

## PUBLIC_DATA_CONTACT is not set on Render

`public_data.public_user_agent()` reads it and omits the contact when it is
missing. Treasury and the ECB do not care. **The SEC does** - it requires an
automated client to declare a contact address and throttles or refuses one
that does not - so this has to be set in the Render dashboard before the
EDGAR fetcher does anything in production. It is deliberately not hard-coded
in the repo; see `SOURCES.md`.

## If the app ever charges, the ECB imposes a second obligation

The ECB permits free use of its reference rates, cited. But: "Where the
information is incorporated in documents that are sold (regardless of the
medium), the natural or legal person publishing the information must inform
buyers, both before they pay any subscription or fee and each time they
access the information taken from this website, that the information may be
obtained free of charge through this website."

Whether a paid app tier is a "document that is sold" is a lawyer question,
recorded in `SOURCES.md`. It is free today, so nothing is owed today. It
becomes a release blocker on the day a price is attached to anything.

## Data sources and licences now live in SOURCES.md

Not parked - done, and pointed at from here so it is findable. `SOURCES.md`
records, per source, what it is, the licence clause verbatim, what the app
relies on, and what attribution is owed. It holds Damodaran's in-person
permission (November 2026), the price-licence status, and the open lawyer
questions.

## EDGAR Stage 1-2 findings, for Stage 3 to act on

The shadow fetcher and the comparison harness are built and read by nothing
in production. The first diff across seventeen companies produced **525
differences, every one classified**. What Stage 3 has to decide, in order of
how much it moves the headline figure:

**1. The base year loses its trailing twelve months, and that is the whole
gap.** EDGAR's annual revenue agrees with the aggregator's annual revenue
within 0.5% in **42 of 57 period-matched years**. But the aggregator's *base
year* is a TTM figure, and EDGAR has no quarters, so the base-year revenue
comes out **-4% to -29%** lower: NVDA -28.7%, BRK-B -43.7%, META -16.8%,
AMZN -10.1%, MSFT -4.2%, AAPL +0.0%. This is not a tagging problem and no
tag chain will fix it. Either the migration reads quarterly facts too (10-Q
for domestic filers, nothing for 20-F filers) or the base year goes back to
annual for everyone, which undoes the TTM work shipped at 1.0.22+23.

**2. Eighty-three tag-chain gaps, each naming the tag to add.** The harness
searches the filing for a tag carrying the aggregator's figure under a
plausible name, so these are a to-do list rather than a complaint. The
largest: `shortTermDebt` (18) wants `OperatingLeaseLiabilityCurrent` for
GOOGL/META/COIN and `CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings`
for BP; `longTermDebt` (10) wants the `...AndCapitalLeaseObligations`
variants; `depreciationAndAmortization` (10) wants
`ifrs-full:DepreciationAndAmortisationExpense` for BP; `interestIncome` (9)
wants `InterestIncomeOther`, `InvestmentIncomeNet`,
`InvestmentIncomeInterestAndDividend`.

**3. JPM's common equity is out by exactly its preferred stock.** EDGAR's
`StockholdersEquity` is 342,393 + 20,045 = 362,438 against the aggregator's
342,393. The DDM measures return on *common* equity, so the chain has to
subtract `PreferredStockValue` rather than pick a different tag. Classified
definitional because no single tag carries the answer; it is still a fix.

**4. Fifteen cases where EDGAR is the better source**, and all fifteen are
the bug class this project has already been bitten by twice - a line the
filer tagged, absent from the aggregator, read downstream as nil.
**Amazon's short-term debt is missing for all four years** ($2.7bn, $5.0bn,
$8.5bn, $3.0bn); Alphabet's for 2025 ($2.0bn); Coinbase's interest income
for all four; Realty Income's notes receivable in 2022 ($933m) where the
aggregator says zero - and a zero on `loans` is how the router decides a
company is not a lender.

**5. The SEC's ticker list can point at the wrong registrant.** XOM maps to
"ExxonMobil Holdings Corporation" (CIK 2115436), a 2026 holding company with
98 tags and no annual revenue; fifty years of accounts sit under "Exxon Mobil
Corporation" (CIK 34088). There is no machine-readable successor link, so
`edgar_data.CIK_OVERRIDES` is hand-maintained and `edgar_financials()`
refuses loudly when a CIK has no annual history. The first version of this
returned an empty company and XOM valued at a 0% operating margin without a
word of complaint. **Every ticker needs this check before a switch.**

**6. ADR ratios and dual-class structures.** TSM's filings are per ordinary
share and the aggregator's are per ADR, five to one; Berkshire's A and B
classes differ by 1,500. The harness measures the ratio from the share
counts and only treats it as a depositary ratio when it lands on a round
number - Tesla measured 0.94, which was a basic-against-diluted mismatch and
not a ratio at all.

**7. Berkshire disagrees by forty per cent on revenue** (247bn against
411bn for 2025) and is the one company where the definitional difference is
large enough to be a different company. Investment gains and the insurance
presentation. Needs its own look.

## Vasicek shrinkage, with a per-industry prior variance

Investigated November 2026 and **not adopted**. A significance-weighted
industry beta was taken instead: the weight on a company's own beta ramps
from nothing at |t| = 1.5 to all of it at |t| = 2.5, blending with the
relevered industry figure between. That is a continuous blend too, so the
cliff this was going to fix no longer exists. What Vasicek would still buy
is a *better reason* for the weight - the ratio of the regression's variance
to the industry's own dispersion, rather than a hand-chosen ramp - and a
prior that is a genuine distribution rather than a point estimate.

**What it is.** Blume shrinks every beta two thirds of the way from 1.0,
whatever the regression was worth. Vasicek shrinks each one toward an
industry prior by *how much the regression is worth*:

    w = s2_prior / (s2_prior + se^2)
    beta_adj = w x beta_raw + (1 - w) x beta_prior

`se` is the standard error of the company's own slope; `s2_prior` is the
variance of betas in the population being shrunk toward. A precisely
measured beta barely moves; a noisy one moves a long way. It is strictly
better reasoning than Blume, which ignores both.

**Why it was not adopted: no single prior variance works.** The pilot ran
all twenty-two check tickers plus Coinbase against three choices of
`s2_prior`:

| s2_prior | where it comes from | LMT | COIN |
| --- | --- | --- | --- |
| 0.072 | spread of Damodaran's 96 industry averages | **0.506** correct | 1.158 wrong |
| 0.250 | halfway | 0.260 wrong | 1.823 wrong |
| 0.716 | spread of individual firms' betas, less mean sampling variance | 0.147 wrong | **2.529** correct |

The two failures are different in kind, which is why no middle value helps.
Coinbase's regression is *precise* - t = 5.7 - so any correct Bayesian
method must leave its 3.46 nearly alone; a tight prior crushes it to 1.16
and moves the headline figure 87%. Lockheed's is imprecise *and centred near
zero*, so only a tight prior rescues it; a loose one leaves it at 0.15,
which is not a beta a defence prime can have. Shrinking correctly is not
enough when the prior itself is a poor description of the company.

**The principled fix, and what it costs.** Vasicek's own formulation wants
`s2_prior` to be the spread *within the company's own industry*, not across
industries or across all firms. Aerospace/Defense clusters tightly, so
Lockheed would shrink hard and land near 0.5; "Investments & Asset
Management" is wildly dispersed across 283 firms, so Coinbase would barely
move. That is the version worth having.

Damodaran's table publishes no dispersion column - only the average - so
this means computing betas for the member firms of each industry: roughly
**twenty firms x ninety-six industries = 1,900 regressions**, cached and
refreshed when the table is. The peer-fetching path built for relative
valuation is the obvious place to hang it. Damodaran does publish
`indname.xls`, which lists the companies in each industry, so the
membership does not have to be guessed.

**The small-industry problem bites this harder than the gate.** A prior
variance estimated from four firms is not a prior variance. Oil/Gas
(Integrated) has four members and is the prior for Exxon, Chevron and BP;
Chemical (Diversified) has four; Rubber & Tires has three; Reinsurance has
**one**. The gate only uses the industry *mean*, which four firms can
support badly but can support. A *variance* from four firms cannot be used
at all, so these industries would need a fallback to a pooled estimate -
another judgement, and one with no obvious right answer.

**Where the working files are.** The pilot script, the full
raw/Blume/Vasicek/WACC/value table for the twenty-two tickers plus Coinbase,
and the SIC-to-industry map it used were written during the November 2026
investigation. The map itself survived: it is now `industry_betas.py`, built
for the S&P 500, so the join work does not have to be redone.

## The industry beta is probably too high for large-cap pharma

Checked November 2026, when the significance ramp shipped. Not a bug, and
not fixed: an open question about the prior itself, with a measurable amount
of money riding on it.

**The finding.** The ten largest US-listed pharmaceutical filers (SIC 2834,
foreign private issuers excluded) have a median raw beta of **0.216** and a
median Blume-adjusted beta of **0.475**. Damodaran's Drugs (Pharmaceutical)
average, relevered on each company's own borrowing, comes out at a median
**0.984** - **2.07x the median Blume figure and 4.55x the median raw one**.
Eight of the ten take that industry figure wholly or mostly.

What it is worth, on Johnson & Johnson:

| beta | where from | WACC | value |
| --- | --- | --- | --- |
| 0.452 | its own, Blume-adjusted (the old rule) | 6.75% | $364.49 |
| 0.964 | Drugs (Pharmaceutical), relevered (shipped) | 8.85% | $218.15 |
| 0.509 | cap-weighted Blume of the ten largest | 6.98% | $361.35 |

So the question "is the industry beta fair for large-cap pharma" is the same
question as "was the old answer right for pharma after all", and 40% of the
headline figure turns on it.

**Why it is probably too high: a composition effect.** Damodaran's average
is **equal-weighted across 228 US drug companies**, and only about a dozen
of those are in the S&P 500. The rest are small, cash-burning,
financing-dependent developers, which are far more market-sensitive than a
$671bn company selling inelastic products. His published levered beta for
the industry is 0.983 - an average company - and large-cap pharma is
defensive by every other measure. The cap-weighted raw beta of the ten
largest is 0.267, in line with their median, so this is not one outlier
dragging an average down; the whole large-cap cohort is low.

**Why it is nonetheless defensible.** The large caps' own betas are not
measurements: median |t| is 1.22 and median R-squared is **0.025**. A beta
of 0.183 +/- 0.15 is not evidence of a low beta, it is an absence of
evidence. An average of 228 noisy betas is a far more precise estimate of
the industry's beta than any single one of them, because the noise cancels
and the signal does not. That is the real statistical argument for the
industry figure, and it stands.

**A structural caveat worth remembering, and not only for pharma.** Since
t = beta / se and the standard errors across this group are all in the
0.15-0.27 band, **t is roughly proportional to beta**: a low beta is
mechanically harder to measure significantly than a high one. The two names
of the ten that clear |t| >= 2.5 (Abbott at 0.505, Zoetis at 0.631) are the
two highest raw betas in the group. So a significance-weighted rule
systematically keeps high betas and replaces low ones, which pushes
defensive companies upward - over and above the composition effect above.
The same mechanism is behind Coca-Cola, Verizon and General Mills moving
the way they did.

**What would settle it.** A size-matched or cap-weighted industry beta.
Damodaran publishes no size breakdown, but he does publish the membership
of each industry at
<https://pages.stern.nyu.edu/~adamodar/pc/datasets/indname.xls>, so a
cap-weighted industry beta could be computed from the member firms. That is
the same infrastructure the parked Vasicek work needs, and doing either
makes the other nearly free.
