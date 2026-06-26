# QuantyxPricer

A multi-model fixed-income and structured-product pricing workspace built on QuantLib and FastAPI, with a React frontend for portfolio management.

---

## Repository Structure

```
QuantyxPricer/
├── assets/                  Bond/note JSON inputs (one file per ISIN)
├── curves/
│   └── swap_curves.json     Named benchmark curves and vol surfaces
├── models/
│   ├── fields/              JSON schema definitions (required + optional) per model
│   ├── helper.py            Shared utilities (date parsing, curve building, day counts)
│   ├── hullwhite.py         Hull-White fixed / floating / callable bond pricer
│   ├── cln.py               Credit-Linked Note pricer (reduced-form hazard model)
│   ├── at1.py               AT1 / CoCo perpetual capital instrument pricer
│   ├── pik.py               PIK and PIK-toggle bond pricer
│   ├── discount_note.py     Discount-note pricer (repos, commercial paper, T-bills)
│   ├── abs.py               ABS / MBS tranche pricer (CPR / PSA pool model)
│   ├── clo.py               CLO tranche pricer (floating-rate, OC-test waterfall)
│   ├── spire.py             SPIRE decomposition pricer
│   ├── index_linked.py      Index-linked / CMS channel-note pricer
│   ├── inflation_linked.py  Inflation-linked bond pricer
│   ├── trinomialtree.py     Trinomial-tree callable bond pricer
│   ├── montecarlo.py        Monte Carlo Hull-White pricer
│   └── convertible/
│       ├── barrier_convertible.py
│       ├── barrier_discount.py
│       ├── simple_convertible.py
│       └── autocallable_reverse_convertible.py
├── api/
│   ├── main.py              FastAPI application (REST endpoints)
│   └── db.py                MySQL persistence layer
├── website/                 React frontend (Vite)
│   └── src/
│       ├── App.jsx
│       ├── Instrument.jsx   Instrument detail and field editor
│       ├── Settings.jsx     Model field settings (required / optional toggles)
│       ├── Sidebar.jsx
│       ├── TimeSeries.jsx
│       └── hooks/
│           └── useAsset.jsx  fetchAsset, fetchModels, fetchAssetFields, updateModel
├── output/                  Generated PDF reports (ISIN.pdf)
├── pricer.py                Root dispatcher — routes by model field in bond JSON
└── scripts/
    └── termsheet_to_json.py PDF termsheet → asset JSON converter
```

---

## Pricing Models

### Rate-based bond pricers

| Model | `"model"` value | Use cases |
|---|---|---|
| Hull-White | `hullwhite` | Fixed / floating / callable corporate bonds, subordinated notes, senior bonds, covered bonds |
| Trinomial Tree | `trinomialtree` | Callable bonds requiring lattice accuracy |
| Monte Carlo | `montecarlo` | Path-dependent instruments, long-dated callables |

### Credit instruments

| Model | `"model"` value | Use cases |
|---|---|---|
| CLN | `cln` | Credit-linked notes, total-return-swap notes (hazard-rate / CDS curve) |
| AT1 | `at1` | Additional Tier 1 / CoCo perpetual bank capital instruments |

### Short-term / money-market

| Model | `"model"` value | Use cases |
|---|---|---|
| Discount Note | `discount_note` | Repos, commercial paper, T-bills, zero-coupon instruments |

### Leveraged / alternative credit

| Model | `"model"` value | Use cases |
|---|---|---|
| PIK | `pik` | Full-PIK bonds, PIK-toggle bonds (leveraged finance, mezzanine, private credit) |
| ABS | `abs` | ABS tranches (auto loans, credit cards, student loans); agency MBS; non-agency MBS |
| CLO | `clo` | CLO tranches (floating-rate pool simulation with reinvestment period + OC test) |

### Structured equity-linked

| Model | `"model"` value | Use cases |
|---|---|---|
| Barrier Convertible | `barrier_convertible` | Convertible bonds with knock-in/knock-out barriers |
| Barrier Discount | `barrier_discount` | Discount certificates with barriers |
| Simple Convertible | `simple_convertible` | Vanilla convertible bonds |
| Autocallable Reverse Convertible | `autocallable_reverse_convertible` | Autocall / reverse convertible structured notes |

### Inflation / index-linked

| Model | `"model"` value | Use cases |
|---|---|---|
| Index Linked | `index_linked` | CMS channel notes, index-linked bonds |
| Inflation Linked | `inflation_linked` | CPI-linked bonds |

### Decomposition

| Model | `"model"` value | Use cases |
|---|---|---|
| SPIRE | `spire` | SPIRE structured product decomposition |

---

## Model Details

### Hull-White (`hullwhite`)

Full-featured DCF pricer for investment-grade bonds. Supports fixed and floating coupons, call schedules, step-up / step-down coupons, CMS-linked coupons, and z-spread discounting. Corporate bonds, subordinated bonds, and covered bonds all use this model.

Key optional features: `call_schedule`, `coupon_structure` (fixed / floating / cms), `day_count`, `business_day_convention`.

### CLN (`cln`)

Prices a CLN as a risky note whose coupons and redemption are weighted by survival probability derived from a piecewise-constant hazard rate term structure calibrated to CDS spreads in the curve file.

`NPV = Σ coupon × S(t) × DF(t) + par × S(T) × DF(T) + recovery × Σ λ × S(t) × DF(t) × Δt`

### AT1 (`at1`)

Prices Additional Tier 1 / CoCo perpetual bank capital instruments. Calculates two scenarios:

- **Price to first call** (`npv_to_first_call`): discounts fixed coupon leg to the first call date.
- **Price to perpetuity** (`npv_to_perpetuity`): projects a reset coupon (using a forward par-swap rate + `reset_spread`) for `perpetuity_horizon_years` years beyond the first call date.

Reports `extension_risk_pct`, `distance_to_trigger` (CET1 headroom above trigger), and `loss_absorption` mode (`write_down` / `equity_conversion`).

### Discount Note (`discount_note`)

Money-market instrument pricer. Supports:

- `discount` (default): zero-coupon; maturity CF = face value.
- `interest_bearing`: add-on interest; maturity CF = face_value × (1 + coupon_rate × t).

Reports `discount_rate`, `simple_yield`, `ytm` (continuously compounded), and `accretion_schedule`.

### PIK (`pik`)

Period-by-period principal accretion model for payment-in-kind instruments.

- `full_pik`: all interest accretes into principal every period. Final redemption = par × compounded factor.
- `pik_toggle`: issuer elects a fraction (`pik_election`) of each coupon to be paid in kind; the remainder is paid in cash. Supports an optional `pik_rate_step_up` for the PIK rate exceeding the cash coupon rate.

Reports `initial_principal`, `final_principal`, `total_pik_accreted`, `accretion_schedule`.

### ABS / MBS (`abs`)

Pool cash-flow model generating a monthly schedule for up to `pool_wam − pool_seasoning` periods. Three instrument types:

| Type | Prepayment | Credit |
|---|---|---|
| `abs` | Flat CPR | CDR + loss severity |
| `mbs_agency` | PSA ramp (default 100 PSA) | None (agency guarantee) |
| `mbs_non_agency` | PSA or CPR | CDR + loss severity |

PSA prepayment ramp: `CPR(m) = min((seasoning + m) / 30, 1) × 6% × psa_speed / 100`.

Sequential waterfall: credit support absorbs losses first; senior tranches (`senior_notes_balance`) receive principal before this tranche.

Reports `wal` (weighted average life), `total_principal_returned`, per-period cashflow detail.

### CLO (`clo`)

Quarterly period-by-period simulation with two phases:

**Reinvestment period** (up to `reinvestment_end_date`): pool principal is reinvested; investors receive only the floating coupon (`fwd_rate + tranche_spread`). Pool balance declines only from CDR defaults.

**Amortisation period** (`reinvestment_end_date` → `maturity_date`): pool amortises linearly over `pool_wal` years. Principal is distributed sequentially (senior tranches first via `senior_notes_balance`).

**OC test** every period: `oc_ratio = pool_balance / total_rated_notes`. If `oc_ratio < oc_threshold` and the tranche is not the most senior (`tranche_is_senior = false`), that period's interest is deferred (diverted to senior paydown).

Floating coupon projected using forward rates from the discount curve: `fwd(t1, t2) = (DF(t1)/DF(t2) − 1) / yearFraction(t1, t2)`.

Reports `wal`, `oc_test_failures` count, per-period breakdown with OC ratio and phase.

---

## Requirements

- Python 3.10+
- QuantLib Python package
- Node.js 18+ (frontend only)
- MySQL 8+ (API persistence)

---

## Setup

### Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install QuantLib numpy fastapi uvicorn python-multipart mysql-connector-python
```

### Frontend

```bash
cd website
npm install
npm run dev        # development server
npm run build      # production build
```

---

## Quick Start

Run one bond by ISIN:

```bash
source .venv/bin/activate
python pricer.py --bond XS3328006716
```

Run all bonds in `assets/`:

```bash
python pricer.py --bond all
```

Start the API server:

```bash
uvicorn api.main:app --reload --port 8000
```

---

## Root Dispatcher (`pricer.py`)

Routes each bond JSON to the correct model based on the `"model"` field. All models share the same invocation interface.

### Parameters

| Flag | Description |
|---|---|
| `--bond` | ISIN, filename, or `all` |
| `--bond-file` | Explicit bond file path |
| `--curve-file` | Curve file path (default `curves/swap_curves.json`) |
| `--all-bonds` | Price every JSON in `assets/` |
| `--issuer-spread-bp` | Override z-spread in basis points |
| `--tree-steps` | Trinomial tree step count override |
| `--time-steps` | Monte Carlo time steps override |
| `--num-paths` | Monte Carlo path count override |
| `--seed` | Monte Carlo random seed override |

### Examples

```bash
python pricer.py --bond FR0013398757
python pricer.py --bond all --curve-file curves/swap_curves.json
python pricer.py --bond XS2148370211 --issuer-spread-bp 120
```

---

## Model Field Schemas (`models/fields/`)

Each model has a corresponding JSON schema in `models/fields/<model>.json` defining its `required_fields` and `optional_fields` with types, descriptions, enums, and defaults. These are served by the `/fetch_models` API endpoint and drive the Settings UI in the frontend.

---

## API Endpoints

The FastAPI server (`api/main.py`) exposes the following tagged endpoints:

### Assets

| Method | Path | Description |
|---|---|---|
| `POST` | `/assets` | Upload and persist a bond JSON |
| `POST` | `/update_asset` | Replace an existing asset by uploaded filename |
| `POST` | `/termsheet_asset` | Upload a PDF termsheet → convert → persist |
| `GET` | `/fetch_asset` | Fetch one asset JSON by `instrument_id` |
| `GET` | `/fetch_assets` | Fetch all assets from MySQL |
| `GET` | `/fetch_models` | Fetch all model field schemas |
| `POST` | `/update_model` | Update required / optional fields for a model |
| `GET` | `/fetch_underlying_assets` | Fetch equity / index underlying assets |
| `GET` | `/fetch_noprice_assets` | List instrument IDs not yet priced |
| `GET` | `/fetch_termsheet` | Download termsheet PDF by instrument_id |
| `GET` | `/fetch_report` | Download output PDF report by instrument_id |

### Pricing

| Method | Path | Description |
|---|---|---|
| `POST` | `/price` | Price one instrument by `instrument_id` (synchronous) |
| `POST` | `/price_all` | Start async batch pricing for all instruments |
| `GET` | `/download_prices` | Download market data for one instrument |
| `GET` | `/download_all_prices` | Download market data for all cached instruments |
| `POST` | `/insert_prices` | Insert a prices JSON payload into MySQL |
| `GET` | `/fetch_prices` | Fetch all prices from MySQL |
| `GET` | `/prices` | Get latest generated `prices.json` |
| `GET` | `/fetch_asset_timeseries` | Fetch price time series for one underlying |
| `GET` | `/fetch_timeseries` | Fetch all price time series |

### General

| Method | Path | Description |
|---|---|---|
| `POST` | `/update_curves` | Start async swap curve update (ECB data) |
| `GET` | `/jobs/{job_id}` | Poll async job status |

---

## Frontend (`website/`)

React SPA (Vite) with hash-based routing.

| Route | Component | Description |
|---|---|---|
| `#/` | `Sidebar` | Portfolio list with search, sort, and price badges |
| `#/instrument/<ISIN>` | `Instrument` | Instrument detail: field table, edit, reprice |
| `#/timeseries/<ISIN>` | `TimeSeries` | Underlying price chart |
| `#/settings` | `Settings` | Model field management: toggle required/optional, add fields, update model |

### Settings page

The Settings page (reached via the Settings button in the sidebar) lets you select any model and manage its field definitions:

- Switch fields between required and optional categories.
- Add new fields with name, type, and description.
- Click **Update** to persist changes via `POST /update_model`.

### Instrument field display

Required fields (as defined in the model schema) are highlighted in **yellow**. Fields present in the asset JSON but not in the schema appear in a third column for traceability.

---

## Input Conventions

- Bond files in `assets/` must be named `<ISIN>.json`.
- Every bond JSON must include a `"model"` field matching one of the model keys above.
- Include `"currency"` (e.g. `"EUR"`, `"USD"`) to drive automatic curve selection.
- Dates accept `DD-MM-YYYY` or `YYYY-MM-DD`.
- Rates accept decimal (`0.045`) or percentage (`4.5`) — models normalise automatically.
- Spreads are always in basis points unless noted otherwise.

---

## Reports

- A PDF report is generated in `output/` for each priced instrument.
- File naming: `<ISIN>.pdf`.
- Reports include input parameters, pricing results, cashflow schedules, and model-specific analytics (accretion tables, OC ratios, extension risk, etc.).

---

## Notes

- Evaluation date is set to today at runtime by each model loader.
- In batch mode (`--bond all`), a failure on one instrument is logged and skipped; the batch continues.
- The discount curve is selected automatically from `curves/swap_curves.json` by matching the bond's `currency` to an OIS curve, or overridden via `"discount_curve_name"` in the bond JSON.
- All models expose `price_asset(bond_data, curve_json)` and `print_result(bond_data, result)` as the standard interface consumed by the dispatcher and the API.
