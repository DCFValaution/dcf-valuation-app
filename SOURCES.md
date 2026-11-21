# Where the data comes from, and what we are allowed to do with it

One file per question a lawyer, a reviewer or a future maintainer would ask.
Written as a record of what was checked and when, not as legal advice — the
places where a reading of the terms is not enough are marked **lawyer
question** and left open rather than answered.

Last reviewed: **16 November 2026.**

---

## 1. Risk-free rate — US Treasury

**Source.** Daily Treasury Par Yield Curve Rates, published by the US
Department of the Treasury.

- Page: <https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve>
- Machine-readable: `daily-treasury-rates.csv/<year>/all?type=daily_treasury_yield_curve`

**Terms.** Works of the US federal government carry no copyright
(17 U.S.C. § 105). Treasury's own data documentation states the data is
"offered free, without restriction, and available to copy, adapt,
redistribute, or otherwise use for non-commercial or commercial purposes."
No key, no registration, no attribution required.

**What we rely on.** The ten-year point only. Quoted on a coupon-equivalent
basis, which is the right basis for a CAPM risk-free rate and differs from
the aggregator's short-bill index (`^IRX`), quoted on a bank-discount basis,
by about twenty basis points at the three-month point.

**Attribution.** Not required. Given anyway, in the provenance line beside
the assumption.

**Status.** Live since Stage 0, November 2026. The aggregator's `^TNX`,
`^FVX`, `^IRX` and `^TYX` quotes remain as the fallback.

---

## 2. Exchange rates — European Central Bank

**Source.** Euro foreign exchange reference rates.

- Page: <https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html>
- Daily feed: <https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml>

**Terms**, from the ECB's Disclaimer & copyright page, quoted:

> Subject to the exception below, users of this website may make free use of
> the information obtained directly from it subject to the following
> conditions: When such information is distributed or reproduced, it must
> appear accurately and the ECB must be cited as the source. Where the
> information is incorporated in documents that are sold (regardless of the
> medium), the natural or legal person publishing the information must inform
> buyers, both before they pay any subscription or fee and each time they
> access the information taken from this website, that the information may be
> obtained free of charge through this website. If the information is
> modified by the user (e.g. by seasonal adjustment of statistical data or
> calculation of growth rates) this must be stated explicitly.

Commercial use is not excluded. Three obligations attach, and all three are
met in code:

1. **Cite the ECB as the source.** Named in the About sheet and in the FX
   provenance shown beside a converted valuation.
2. **If ever sold, tell buyers it is free from the ECB.** Not applicable
   today — the app is free. **It becomes a release blocker the day the app
   charges for anything**, including a subscription that merely includes the
   valuation. See `PARKED.md`.
3. **State any modification explicitly.** Every pair that does not involve
   the euro is a **cross-rate we computed** from two ECB quotes, not a figure
   the ECB published. `public_data.ecb_fx_rate()` labels it as such and the
   honesty note repeats the label.

**Coverage.** 29 currencies against the euro. **The New Taiwan dollar is not
among them**, nor the Saudi riyal or the UAE dirham. TSM reports in TWD and
trades in USD, so it takes the aggregator fallback — a live path, not a
theoretical one.

**Caveat on the number itself.** These are reference rates fixed at 14:15 CET
on TARGET working days, for information purposes. They are explicitly not
tradeable rates and not a market close. For restating a full year of accounts
at one date that distinction is immaterial; it would not be for anything
settling a transaction.

**Status.** Live since Stage 0, November 2026. The aggregator's `=X` pair
quotes remain as the fallback.

---

## 3. Betas, equity risk premium, industry data — Aswath Damodaran

**Source.** The datasets published at
<https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html>

**Permission.** **Professor Damodaran gave permission in person, in November
2026, to use his published data in this app with attribution.** That
permission is the basis on which the implied equity risk premium and the
industry beta tables are used.

**What we rely on.**

| Input | Dataset | As of |
| --- | --- | --- |
| Implied equity risk premium (4.41%) | Implied ERP by month | November 2026 |
| Industry unlevered betas | Betas by Sector (US) | January 2026 |

**Attribution.** Required by the permission. Given in the ERP provenance
line ("Damodaran's implied premium…") and in the About sheet.

**Which column, and why.** The industry table is taken from
`betas.xls`, sheet "Industry Averages", column **"Unlevered beta corrected
for cash"**. That is Damodaran's own recommendation, stated on the sheet's
first rows: *"if you are looking for a pure-play beta, i.e., a beta for a
business, the unlevered beta corrected for cash is your best bet."* An
unlevered beta is the industry's business risk with its own financing
stripped out, which is what can be relevered onto a particular company;
correcting for cash removes the drag of balance-sheet cash, which is a
financing choice rather than a business one.

Relevering uses **0.25**, the flat marginal rate Damodaran unlevers with
(stated on the spreadsheet's own "Inputs" sheet). Using a company's
effective rate instead would relever by a different formula from the one
the figure was unlevered by, and the difference would read as business
risk when it is only a mismatch of tax definitions.

**Refresh.** The ERP is a configured constant with an as-of date and a
staleness flag, not a fetched value — see `assumptions.ERP_STALE_AFTER_DAYS`.
Monthly refresh is tracked in `PARKED.md`.

The industry betas are likewise baked in, as `industry_beta_data.py`, and
regenerated by `tools/refresh_industry_betas.py`. Damodaran recomputes them
in the first week of January and leaves them at the same URL all year, so
this is an annual job. Baking them in is also what makes a report
reproducible: the same ticker on the same day gives the same answer whether
or not NYU's server is up. `--check` compares the committed table against
the live spreadsheet without rewriting it.

**The join is ours, and it is a judgement.** Damodaran's industries are his
own taxonomy, and the only industry label that arrives with a company's own
filings is the SEC's SIC code. The map between them is hand-written, in
`industry_betas.SIC_TO_INDUSTRY`, covering the 184 distinct SIC codes used
by the S&P 500 plus this project's check tickers. Thirty-four entries where
the code's plain meaning and Damodaran's taxonomy disagree are recorded in
`SIC_JUDGEMENTS` with the alternative that was rejected. Attribution covers
his figures; the mapping of a company to one of his industries is this
project's claim and not his.

---

## 4. Company fundamentals — commercial aggregator

**Status: the known problem, and the reason for the EDGAR migration.**

All statement data, prices, share counts, dividends and beta history come
from a commercial aggregator's undocumented endpoints. Its terms of service
(§2, items 9 and 10, verified directly, last updated 1 November 2026)
prohibit automated collection "for any purpose" and prohibit mobile
applications that substitute for the service or its data providers.

**This is not a licence we hold, and it is not one we can obtain.** It is the
single blocker on a public release. The replacement programme:

- **Fundamentals → SEC EDGAR.** Public domain, explicitly permitted for
  automated access with a declared User-Agent. Covers US filers fully;
  foreign private issuers file annually on Form 20-F, so quarterly and TTM
  figures are not available for them. Staged migration in progress — see §5.
- **Prices → a paid end-of-day licence.** See §6.
- **Rates and FX → done.** §1 and §2 above.

---

## 5. SEC EDGAR — the fundamentals replacement

**Source.** XBRL company facts.

- Ticker to CIK: <https://www.sec.gov/files/company_tickers.json>
- Per company: `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
- Nightly bulk archive of every filer (~1.5 GB), opt-in via `EDGAR_BULK_FACTS`
- Registrant record, for the assigned SIC code:
  `https://data.sec.gov/submissions/CIK##########.json`

**The SIC code is used in production; the facts are not.** The submissions
endpoint is the only source of the industry label that arrives with a
company's own filings, and `industry_betas.py` needs it to pick a Damodaran
industry. It is read by `tools/refresh_company_sic.py` and baked into
`company_sic_data.py`, so no valuation makes an SEC request: the universe
is the S&P 500 plus this project's check tickers, and a ticker outside it
falls back to the whole-market beta. That also keeps the test that forbids
production from importing `edgar_data` true.

The S&P 500 membership list used to define that universe comes from
<https://en.wikipedia.org/wiki/List_of_S%26P_500_companies> (CC BY-SA 4.0).
Only the ticker symbols and CIKs are taken — facts, not expression — and
only by a build tool, never at valuation time. The index name itself is
S&P's trademark and is not used in anything the app shows.

**Terms.** Public domain as a work of the US federal government
(17 U.S.C. § 105). Automated access is explicitly permitted, subject to the
SEC's Fair Access guidance: no more than ten requests a second, and the
client must declare itself.

**The declaration is not optional, and its shape matters.** Measured
16 November 2026: the SEC serves a User-Agent that looks like a contact
address — their own documented example is `Sample Company Name
AdminContact@<domain>.com` — and returns **HTTP 403, "Your Request
Originates from an Undeclared Automated Tool"**, to one that looks like a
crawler. Our project string, `dcf-valuation-app/1.0 (+https://github.com/...)`,
is refused. So `PUBLIC_DATA_CONTACT` must be set to a real contact address
or there is no EDGAR data at all. It is deliberately not committed to this
repository; see `PARKED.md`.

**Status.** Shadow fetcher (`edgar_data.py`) and comparison harness
(`tools/edgar_compare.py`) built November 2026. **Nothing in production
reads them** — a test asserts that no production module imports
`edgar_data`. Switching over is a later, separate decision.

**What EDGAR cannot supply**, and therefore what a switch does not cover:
prices, market capitalisation, beta, price history; the sector and industry
labels the method router needs; quarterly facts for foreign private issuers,
and so no trailing-twelve-month base for them; and the normalised lines the
aggregator computes but no filer reports (a single "total debt", a
"reconciled depreciation", an effective tax rate for calculations).

---

## 6. Share prices — the one input still bought

**Decision, 20 November 2026: the app launches publicly with prices from
Yahoo.** Everything else has moved to a licensed primary source — statements
to SEC EDGAR, the risk-free rate to the US Treasury, exchange rates to the
ECB, industry betas to Damodaran with his permission. Prices are what is
left, and §6b below is why there is no free licensed alternative to move
them to.

### Swapping the provider is one class and one setting

Every price request in the app goes through `price_source.py`:

| Question | Who asks |
| --- | --- |
| `latest_close(symbol)` | the company profile, the relative tab's figures, the FX fallback, the Treasury-yield fallback |
| `monthly_closes(symbol)` | the beta regression, for the company and for `^GSPC` |
| `dividend_events(symbol)` | the dividend discount model |
| `daily_close(symbol)` | FX pairs and yield indices, behind their licensed primaries |

`YahooPriceSource` is the only implementation. Adding Twelve Data means
writing one class, adding it to `_SOURCES`, and setting `PRICE_SOURCE` on
Render. A test (`test_price_source.test_no_other_module_fetches_a_price`)
asserts that no other module reads a chart URL, a `regularMarketPrice` or a
price history, so the single door cannot quietly grow a second one.

What is **not** behind it, and so is separate work for a switch: symbol
search, the "also watched" peer suggestions, and the fundamentals
timeseries. Those are the same provider's endpoints but they are not prices,
and folding them in would have made this "the Yahoo module" rather than
"the price module".

### Caching, and why on the market's clock

A close is good until the next one exists, so the latest close is cached
until the next US regular-session close rather than for a fixed period.
Caching "for an hour" either serves a stale price after the bell or
re-fetches an unchanged one all evening. Monthly histories are cached for a
day; five years of month-end closes cannot move materially in less.

Measured over a session of three companies, five valuations each, plus the
relative tab, with Yahoo's quote endpoint unavailable as it is on Render:
**46 price requests before, 7 after.**

In memory, deliberately: the free Render instance restarts on its own
schedule, so a disk or Redis cache would add a dependency to buy back
something the platform takes away anyway.

### Every price is dated

`close as of 20 November 2026`, beside the price everywhere it appears. With
a cache, the figure on screen is frequently a previous session's and nothing
else would say so. The date is never defaulted to today — a price whose date
the provider did not send reads "close date unavailable", because relabelling
a stale price as current is the one mistake the field exists to prevent.

---

## 6b. Price licence status

**As of 16 November 2026.**

| Provider | Status |
| --- | --- |
| **Marketstack** | Emailed. Awaiting reply. |
| **EODHD** | Emailed. Awaiting reply. |
| **MarketData.app** | **The fallback.** Commercial Plan, $333/month, display licence and exchange fees included, 14-day trial, no long-term contract. Not yet contacted. |
| **Intrinio** | Startup tier at the same entry price. The hedge if MarketData.app falls through. |
| **Twelve Data** | Researched; priced above the alternatives for the same permission. |

### Free sources: ruled out

Researched November 2026. The structural finding is that exchanges and
vendors give the *data* away cheaply and charge for the *right to display it
to users*, so a free display licence does not exist to be found.

**IEX Exchange.** The fee schedule genuinely lists delayed TOPS/DEEP/DEEP+ as
free. Three findings kill it:

- The IEX Market Data Policies (September 2026) state that Delayed IEX Market
  Data "cannot be obtained directly from IEX and must instead be obtained
  through another Data Subscriber, such as a market data provider." Free
  means no fee, not self-serve — a vendor is still required.
- "IEX Historical Data" is defined as what is published at
  `iextrading.com/stats/#historical-stats`, which carries **volume and
  market-share statistics, no prices**. The by-symbol downloads have no price
  column. The old pcap archive at `iextrading.com/api/1.0/hist` returns
  HTTP 403, and no equities HIST product is listed on the current site.
- IEX is **4.25% of US volume** (their own stats, 13 November 2026), so an
  IEX last sale is not the consolidated official close in any case.

Had delayed data been obtained through a vendor, §5 of the Policies would
require attribution on every display including mobile, with the specific
wording **"IEX Delayed Price and Last Sale"**, plus a delay disclaimer.

Two clauses worth keeping on file: §11 defines **Derived Data** as output
that cannot be reverse-engineered back to the source — a DCF intrinsic value
qualifies — and permits a subscriber to distribute it externally free of
charge without the recipient signing anything. §12 prohibits using IEX data
to create or calculate "any index or similar work" without prior written
authorisation.

**TradingView widgets.** Free to embed, attribution required by §4
("Charts by TradingView", branding unaltered). Extraction is prohibited
outright: §3 bars non-display usage including "price referencing" and any
processing of their content; §12 bars scrapers; §11 states raw data is never
accessible and may not be reverse-engineered. So a widget can sit beside a
valuation as a chart and can never feed one.

**Others checked and rejected.** Cboe requires advance written approval.
FINRA's free equity datasets are short interest and OTC transparency, not NMS
closing prices, and its data portal states non-commercial use. Nasdaq Data
Link returned no readable terms. Stooq served an empty body.

### Lawyer questions, open

- Whether a **published per-share intrinsic value** is an "index or similar
  work" under IEX Policies §12, or an "indicative value" under the
  equivalent clause in Massive's terms (§6.1(f)). The same question in two
  contracts, and it decides whether the derived-data route is open at all.
- Whether the ECB's "inform buyers" obligation (§2 above) is triggered by a
  paid tier of an app that also shows ECB-derived figures, as opposed to a
  document sold.
