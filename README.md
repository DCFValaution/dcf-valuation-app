# DCF Valuation

A discounted cash flow valuation service for listed companies, plus an Android
app that consumes it.

Assumptions are derived from each company's own filings and labelled with
where they came from, and companies a growth-perpetuity DCF does not suit —
loss-making businesses, banks and insurers — are refused rather than given a
confident but meaningless number.

## Layout

| Path | What it is |
|---|---|
| `dcf.py` | The DCF engine. Pure arithmetic, no I/O. |
| `market_data.py` | Yahoo Finance access, caching and error handling. |
| `assumptions.py` | Derives per-company assumptions from the filings. |
| `analysis.py` | Suitability guard and orchestration. |
| `api.py` | FastAPI web layer. |
| `excel_export.py` | Builds the downloadable live-formula workbook. |
| `app/` | The Flutter app. |
| `tools/` | Fixture generation and the app icon generator. |
| `AAPL_DCF_Model.xlsx` | Reference model the engine is tested against. |

## Running locally

```bash
pip install -r requirements.txt -r requirements-dev.txt
uvicorn api:app --reload --port 8000
```

Then open <http://localhost:8000/docs>.

**No API key is required.** Market data comes from Yahoo Finance via
`yfinance`, which needs no credentials.

## Tests

```bash
python -m pytest -q
```

These run offline — Yahoo is stubbed. Two scripts hit the live service and
are not part of the suite:

```bash
python test_market_data_live.py     # reconciliation, coverage, guards
python test_analysis_live.py AAPL   # a full valuation, printed
```

## Deploying

`render.yaml` configures a Render web service. Push the repository to GitHub,
then in Render choose **New → Blueprint** and point it at the repo.

Two things to know about the free tier:

- **It sleeps.** A free instance spins down after about 15 minutes idle, and
  the next request waits roughly a minute for a cold start.
- **Shared egress IPs.** Yahoo throttles by IP, so a cloud host can be rate
  limited where a laptop is not. `market_data.py` caches responses to keep
  request volume down, and surfaces throttling as HTTP 429.

### Yahoo from a datacentre IP

Yahoo does not refuse a cloud host everything. Measured from Render, it
serves the search, chart and fundamentals endpoints normally but mints the
crumb that authenticates its quote endpoint roughly once in fourteen
attempts. The statements therefore arrive fine while `yfinance`'s `.info`
fails, which is why:

- requests go out on a shared `curl_cffi` session impersonating Chrome, as
  Yahoo fingerprints the TLS handshake and not merely the User-Agent;
- an unavailable profile falls back to `crumb_free_profile()`, which
  rebuilds it from chart and search. **Beta is not recoverable this way, so
  WACC falls back to its documented default** and the response says so;
- an empty statement frame is retried, because a throttled request returns
  an empty DataFrame rather than raising.

`GET /diagnostics/upstream?ticker=AAPL` reports what Yahoo returns to the
*server*, which is the only place that question can be answered. Use it
before concluding anything about a failure.

#### Beta is computed, not quoted

Because the quote endpoint answers only when the crumb happens to mint,
trusting Yahoo's published beta left the figure — and the valuation —
depending on which endpoint Yahoo felt like serving. AAPL came back 1.085
quoted and 1.088 computed on the same afternoon; before that, most requests
lost beta entirely and fell back to 1.0, which moves WACC by several hundred
basis points.

`fetch_beta()` therefore computes it from the chart endpoint, using Yahoo's
own convention — five years of monthly returns against `^GSPC`. Checked
against their published figure for fifteen tickers: **mean absolute
difference 0.037**, max 0.162. The computed value is preferred even when a
quoted one is available, because determinism is the point; a quoted beta is
used only if the computation fails, and 1.0 only if both do.

`wacc_inputs` in the response says which path produced the number.

Errors distinguish a symbol Yahoo positively denies (404) from one it will
not currently serve (429/502). An empty response is never reported as an
unknown ticker.

## The app

`app/lib/valuation_api.dart` holds `kBackendBaseUrl`, which defaults to the
deployed service — a distributed build cannot reach a server on the
developer's machine. To run against a local backend:

```bash
flutter run --dart-define=BACKEND_BASE_URL=http://10.0.2.2:8000
```

`10.0.2.2` is the Android emulator's alias for the host machine; `localhost`
would resolve to the emulator itself.

### Cold starts

The free instance sleeps after about fifteen minutes idle and takes roughly a
minute to wake, so the app:

- allows **90 seconds** for a valuation and 120 for an Excel export (at the
  old 30 seconds the first request of a session failed reliably);
- pings `/health` on launch, so the spin-up overlaps with the user typing a
  ticker rather than being paid for by their first valuation;
- switches the spinner to *"Waking up the server — this can take up to a
  minute on the first use after a while"* once a request passes four seconds.

The wait is the same either way; only one version of it is legible.
