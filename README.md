# YottaFlex Cold-Chain Shipment Compliance & Deviation Management

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Zero External Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Stdlib%20Only)-brightgreen.svg)](#1-quick-start--make-it-run)
[![Seed 20261022](https://img.shields.io/badge/Fixed%20Seed-20261022-purple.svg)](#6-reproducibility--validation-proof-steps-8--10)
[![Validation Status](https://img.shields.io/badge/Validation-100%25%20Passing-success.svg)](docs/VALIDATION_SUMMARY.md)

**End-to-End Solution for Temperature-Sensitive Pharmaceutical & Biologic Shipment Compliance, Excursion Evaluation, Traceability, and Deviation Management.**

---

## Table of Contents
1. [Quick Start — Make It Run](#1-quick-start--make-it-run)
2. [Problem Statement, Actors & Business Workflow](#2-problem-statement-actors--business-workflow)
3. [Relational Schema, Nomenclature & Bridge Tables](#3-relational-schema-nomenclature--bridge-tables)
4. [Thermodynamic & Compliance Decision Engine (Why & How)](#4-thermodynamic--compliance-decision-engine-why--how)
5. [Controlled Negative Edge Cases & Ground-Truth Isolation](#5-controlled-negative-edge-cases--ground-truth-isolation)
6. [Reproducibility & Validation Proof (Steps 8 & 10)](#6-reproducibility--validation-proof-steps-8--10)
7. [Design Decisions, Assumptions, Trade-offs & Limitations](#7-design-decisions-assumptions-trade-offs--limitations)
8. [Public Data Guidance & Lawful Provenance](#8-public-data-guidance--lawful-provenance)
9. [Repository Structure](#9-repository-structure)

---

## 1. Quick Start — Make It Run

This project uses **100% Python Standard Library (`csv`, `json`, `sqlite3`, `math`, `hashlib`, `datetime`, `unittest`)** with **zero external third-party package dependencies** so any reviewer can clone and run it immediately on Windows, macOS, or Linux.

### Prerequisites
- Python `3.10+` (tested on Python `3.14.5`)

### Step 1: Run the End-to-End Pipeline (Generates Starter & Full Datasets + Runs All Validations)
```bash
python -m src.cli --seed 20261022
```
What this single command does in ~20 seconds:
1. Generates the **Starter Dataset** (`250` shipments, `100` loggers, `4,666` 10-minute readings) in `data/starter/` (`CSV`, `JSON`, `SQLite DB`, `SQL seed`, `ground_truth`, and `source_manifest.json`).
2. Generates the **Full Baseline Dataset** (`5,000` shipments, `1,000` loggers, `93,395` 10-minute readings, `185,785` total records) in `data/full/`.
3. Executes the **Step 8 Validation Suite** (`314,700` foreign-key integrity checks, primary-key regex checks, arithmetic reconciliations, lifecycle DAG checks, and independent anomaly audits).
4. Executes the **Step 10 Reproducibility Proof** (regenerates twice with `--seed 20261022` and compares row counts and SHA-256 checksums across all exported files).
5. Writes [`docs/VALIDATION_SUMMARY.md`](docs/VALIDATION_SUMMARY.md) and [`docs/VALIDATION_REPORT.json`](docs/VALIDATION_REPORT.json).

### Step 2: Run the Automated Unit & Integration Test Suite
```bash
python -m unittest discover -s tests -v
```

### Step 3: Run Individual Generator, Validator, or Shipment Inspector CLI Commands
```bash
# Generate only the starter dataset (250 shipments, 100 loggers)
python -m src.generator --seed 20261022 --scale starter --output-dir data/starter

# Generate the full 5,000-shipment, 1,000-logger dataset
python -m src.generator --seed 20261022 --scale full --output-dir data/full

# Run the standalone validator & two-run reproducibility verifier
python -m src.validator --seed 20261022 --scale full --report-out docs/VALIDATION_REPORT.json

# View the Operations Manager & Analyst portfolio summary and exception queue
python -m src.inspector --scale starter --list-exceptions

# Inspect a complete traceability dossier for any individual shipment (e.g. an injected anomaly)
python -m src.inspector --scale starter --shipment-id SHP-2026-000018
```

---

## 2. Problem Statement, Actors & Business Workflow

### Business Objective
Prove that temperature-sensitive shipments (biologics, mRNA vaccines, monoclonal antibodies, insulin peptides, blood plasma) remained within product and route requirements from pack-out through final receipt, and manage deviations with full audit traceability.

### Primary Actors & Responsibilities
| Actor Role | System Responsibility & Decisions Recorded |
| :--- | :--- |
| **`SUPPLIER`** | Manufactures `Product` lots (`Lot`), verifies thermal `Packaging` qualification at pack-out (`PACKOUT_SEALED`), and transitions shipment from `PLANNED -> PACKED`. |
| **`PLANNER`** | Schedules `Shipment` routes (`RouteStop`), assigns product `ThresholdPolicy`, and binds calibrated `Logger` units via `ShipmentLogger`. |
| **`CARRIER_OPERATOR`** | Executes physical pickup (`CARRIER_PICKUP`), air/road/multimodal transit, and hub transfers (`IN_TRANSIT`). |
| **`WAREHOUSE_OPERATOR`** | Logs transit hub arrivals (`HUB_ARRIVAL_SCAN`), cold-room storage or tarmac dwell events, and destination receipt (`FINAL_RECEIPT`, `DELIVERED`). |
| **`LOGISTICS_ANALYST`** | Evaluates 10-minute `TemperatureReading` telemetry, flags missing-data or custody gaps, and dispositions minor excursions (`RELEASE_WITH_STABILITY_DEDUCTION`, `QA_MANUAL_REVIEW`). |
| **`OPERATIONS_MANAGER`** | Reviews critical excursions and missing-logger anomalies, issuing final `QUARANTINE_HOLD` or `REJECT_DESTROY` decisions (`CLOSED_QUARANTINED`, `CLOSED_REJECTED`). |

---

## 3. Relational Schema, Nomenclature & Bridge Tables

Full field-level rules, data types, units, enums, and constraints are documented in [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md).

```mermaid
erDiagram
    Product ||--o{ ThresholdPolicy : "1:N"
    Product ||--o{ Lot : "1:N"
    ThresholdPolicy ||--o{ Shipment : "1:N"
    Packaging ||--o{ Shipment : "1:N"
    Shipment ||--|{ ShipmentLot : "1:N"
    Lot ||--o{ ShipmentLot : "1:N"
    Shipment ||--o{ ShipmentLogger : "1:N"
    Logger ||--o{ ShipmentLogger : "1:N"
    Shipment ||--|{ RouteStop : "1:N"
    Shipment ||--|{ CustodyEvent : "1:N"
    RouteStop ||--o{ CustodyEvent : "1:N"
    Logger ||--o{ TemperatureReading : "1:N"
    Shipment ||--o{ TemperatureReading : "0..1:N"
    Shipment ||--|| ExcursionReport : "1:1"
    Shipment ||--|{ ShipmentStateHistory : "1:N"
```

### Strict Deterministic ID Nomenclature (Step 3)
| Entity | Nomenclature Pattern | Example ID | Full Scale Rows | Starter Scale Rows |
| :--- | :--- | :--- | ---: | ---: |
| `Product` | `PROD-2026-<0001..0010>` | `PROD-2026-0001` | `10` | `10` |
| `ThresholdPolicy` | `POL-2026-<0001..0010>` | `POL-2026-0001` | `10` | `10` |
| `Packaging` | `PKG-2026-<0001..0010>` | `PKG-2026-0001` | `10` | `10` |
| `Lot` | `LOT-2026-<6-digit>` | `LOT-2026-000001` | `400` | `50` |
| `Logger` | `LOG-<shipment-id>-01` | `LOG-SHP-2026-000001-01` | `1,000` | `100` |
| `Shipment` | `SHP-2026-<6-digit>` | `SHP-2026-000001` | `5,000` | `250` |
| `ShipmentLot` *(Bridge)* | `SHPLOT-2026-<6-digit>` | `SHPLOT-2026-000001` | `6,000` | `300` |
| `ShipmentLogger` *(Bridge)* | `ASSGN-2026-<6-digit>` | `ASSGN-2026-000001` | `4,950` | `247` |
| `RouteStop` | `STOP-<shipment-id>-<01..03>` | `STOP-SHP-2026-000001-01` | `15,000` | `750` |
| `CustodyEvent` | `CUST-2026-<7-digit>` | `CUST-2026-0000001` | `15,000` | `750` |
| `TemperatureReading` | `TEMP-<logger-id>-<YYYYMMDDHHMMSS>` | `TEMP-LOG-SHP-2026-000001-01-20260102060000` | `93,395` | `4,666` |
| `ExcursionReport` | `EXCR-2026-<6-digit>` | `EXCR-2026-000001` | `5,000` | `250` |
| `ShipmentStateHistory` | `STHIST-2026-<7-digit>` | `STHIST-2026-0000001` | `30,000` | `1,500` |
| `GroundTruth` *(Separate)* | Keyed by `shipment_id` | `SHP-2026-000001` | `5,000` | `250` |

---

## 4. Thermodynamic & Compliance Decision Engine (Why & How)

All metrics in `ExcursionReport` are derived directly from source `TemperatureReading`, `CustodyEvent`, and `ThresholdPolicy` records in [`src/compliance_engine.py`](src/compliance_engine.py):

1. **Mean Kinetic Temperature (MKT) via Haynes Formula (USP \<1079\>)**:
   $$\text{MKT (K)} = \frac{\Delta H / R}{-\ln \left( \frac{1}{N} \sum_{i=1}^{N} e^{-\Delta H / (R \cdot T_i)} \right)}$$
   where $\Delta H = 83.14472\text{ kJ/mol}$ and $R = 0.008314472\text{ kJ/(mol}\cdot\text{K)}$. Because MKT weights higher temperatures exponentially, brief thermal spikes have a higher kinetic impact than arithmetic averages reveal.
2. **Excursion Duration & Contiguous Window Calculation**:
   - Readings within the inclusive range `[min_temp_c, max_temp_c]` are compliant; readings strictly outside (`< min_temp_c` or `> max_temp_c`) are breaches.
   - For standard 10-minute intervals, each breaching reading increments `total_excursion_duration_min` by `10` minutes and tracks `max_single_excursion_min` (longest contiguous breach) and `first_breach_reading_id`.
   - When an excursion spans across irregular or missing telemetry intervals ($\Delta t > 10\text{ min}$), `ComplianceEngine` computes the actual elapsed timestamp difference rather than understating the breach as a fixed 10-minute step, flags `uncertainty_borderline_flag = True`, and routes the shipment to `NON_COMPLIANT_MISSING_DATA` / `QUARANTINE_HOLD` (or `CRITICAL_EXCURSION` if thresholds are exceeded).
3. **Calibration Uncertainty Borderline Rule**:
   - Every logger carries a NIST-traceable `calibration_uncertainty_c` ($\pm 0.15^\circ\text{C}$ to $\pm 0.50^\circ\text{C}$). When a reading lies within $0 < |T - T_{\text{limit}}| \le \text{uncertainty}$, `uncertainty_borderline_flag` is set to `True` and routed to `INVESTIGATION_REQUIRED` / `QA_MANUAL_REVIEW`.
4. **Expected Time-Grid Telemetry, Duplicate Detection & Custody Gap Rules**:
   - Expected readings are verified against the explicit 10-minute timestamp grid $\{t_{\text{dep}}, t_{\text{dep}}+10\text{m}, \dots, t_{\text{arr}}\}$ rather than raw row counts.
   - Duplicate reading IDs or duplicate timestamps per logger are detected and deduplicated before grid comparison so duplicate rows can never mask missing intervals.
   - Any missing-data window $\ge \text{max\_telemetry\_gap\_min}$ (inclusive exact boundary) or incomplete grid coverage sets `telemetry_gap_flagged = True` (`NON_COMPLIANT_MISSING_DATA`).
   - Any custody handover delay $> \text{max\_custody\_gap\_min}$ (configurable per `ThresholdPolicy`, default `120` min) sets `custody_gap_flagged = True` (`NON_COMPLIANT_CUSTODY_GAP`).
5. **Product-Policy Compatibility & Cross-Shipment Isolation**:
   - `ComplianceEngine.evaluate_shipment()` verifies that `policy.product_id` matches the shipment's `expected_product_id`, that the shipment window falls within `[policy.effective_from, policy.effective_to]`, and that readings belong strictly to `expected_shipment_id` and `expected_logger_ids` within `[actual_departure_utc, actual_arrival_utc]`. Any mismatch or foreign reading triggers `INVESTIGATION_REQUIRED` / `QUARANTINE_HOLD`.

---

## 5. Controlled Negative Edge Cases & Ground-Truth Isolation

In accordance with **Step 5** and **Step 7**, valid baseline records are generated first, and **5 specific negative system/process edge cases** (`50` shipments each in full scale, `250` total; `100%` isolated labels in `ground_truth.csv` / `ground_truth.json`) are injected:

| Edge Case Label (`edge_case_type`) | Count (Full) | How It Manifests in Input Data | How the Independent Auditor Detects It |
| :--- | ---: | :--- | :--- |
| `LOGGER_NOT_ASSIGNED` | `50` | No row in `ShipmentLogger` for the shipment; logger records 3 orphan readings with `shipment_id = NULL`; `ExcursionReport.logger_id_evaluated = NULL`. | Joins `Shipment` to `ShipmentLogger` and checks for missing logger binding & orphan readings. |
| `PRODUCT_THRESHOLD_MISMATCH` | `50` | `Shipment.assigned_policy_id` points to a policy belonging to a different product temperature regime than `Shipment.primary_product_id`. | Compares `Shipment.primary_product_id` against `ThresholdPolicy.product_id`. |
| `MISSING_DATA_SILENTLY_PASSED` | `50` | 5 consecutive 10-minute readings are dropped (`50-min` gap $>$ policy limit), but `ExcursionReport` leaves `telemetry_gap_flagged = False` and marks `COMPLIANT`. | Recomputes `elapsed_since_prev_min` from raw `TemperatureReading` timestamps and compares against policy. |
| `CUSTODY_GAP_NOT_FLAGGED` | `50` | Transit hub `CustodyEvent.handover_gap_min = 165` min ($> 120$ min limit), but `custody_gap_flagged` is left `False` in both `CustodyEvent` and `ExcursionReport`. | Audits raw `CustodyEvent.handover_gap_min > 120` against `ExcursionReport.custody_gap_flagged`. |
| `CROSS_SHIPMENT_EXCURSION_CALC` | `50` | `ExcursionReport.total_excursion_duration_min` includes excursion minutes from a prior shipment that used the same physical `logger_id`, falsely triggering `REJECT_DESTROY`. | Recomputes excursion duration strictly filtering `TemperatureReading` by `shipment_id` and `actual_departure_utc .. actual_arrival_utc`. |

---

## 6. Reproducibility & Validation Proof (Steps 8 & 10)

Full validation results are available in [`docs/VALIDATION_SUMMARY.md`](docs/VALIDATION_SUMMARY.md) and [`docs/VALIDATION_REPORT.json`](docs/VALIDATION_REPORT.json).

- **Primary Key & Nomenclature Regex**: `100% PASS` (`0` errors across all `13` tables / `180,785` input rows).
- **Foreign Key Integrity**: `100% PASS` (`314,700` foreign key references verified with `0` orphans).
- **Arithmetic & Lifecycle Reconciliation**: `100% PASS` (`5,000` shipment declared values reconciled to `SUM(lot_qty * unit_value_usd)`, `15,000` route stop dwells reconciled, `30,000` state transitions verified).
- **Independent Audit vs. Ground Truth**: `5,000 / 5,000` (`100.0%`) exact match on compliance status, disposition decision, and injected anomaly detection.
- **Step 10 Fixed-Seed Reproducibility**: Regenerating twice with `--seed 20261022` produces **100% identical row counts and SHA-256 checksums** across all `29` exported CSV, JSON, SQL, and Ground Truth files.

---

## 7. Design Decisions, Assumptions, Trade-offs & Limitations

### Key Design Choices & Assumptions
1. **Logger Reuse Across Non-Overlapping Waves (`5,000` Shipments / `1,000` Loggers)**:
   - To satisfy both `5,000 shipments, 1,000 loggers` and the ID convention `LOG-<shipment-id>-01`, each of the `1,000` loggers is named after its initial commissioning shipment (`LOG-SHP-2026-000001-01` .. `LOG-SHP-2026-001000-01`) and assigned across `5` non-overlapping 24-hour dispatch waves via the `ShipmentLogger` (`N:M`) bridge table.
2. **State History Preservation (Rule 4)**:
   - Rather than only storing a mutable status column, every state transition (`PLANNED -> PACKED -> IN_TRANSIT -> DELIVERED -> EVALUATED -> CLOSED_*`) is persisted as an immutable event record in `ShipmentStateHistory`.
3. **Zero Third-Party Dependencies**:
   - Built strictly with Python's standard library so reviewers never face `pip` compilation issues or environment conflicts.
4. **SQLite Foreign-Key Enforcement (`PRAGMA foreign_keys = ON`)**:
   - Because SQLite resets `PRAGMA foreign_keys = 0` on newly opened connections by default, [`open_sqlite_db()`](src/generator.py) explicitly enables and verifies `PRAGMA foreign_keys = ON;` on every database connection. Consumers opening `cold_chain_compliance.db` directly via `sqlite3` should either call `open_sqlite_db()` or run `PRAGMA foreign_keys = ON;` upon connection.
5. **CLI & Programmatic Architecture (Scope Clarification)**:
   - Per the YottaFlex AI FAQ (#9: *"No hosted demo or automated test suite is mandatory"*), this platform is architected as a self-contained CLI and data-engineering pipeline rather than a browser web app, complete with an Operations Manager & Analyst CLI Inspector (`python -m src.inspector`) for inspecting shipment dossiers and exception queues.

### Trade-offs & Known Limitations
- **Trip Duration Scaling for Disk Footprint**: Trip durations are modeled between `2.0` and `4.0` hours (`13` to `25` readings at 10-minute intervals, yielding `93,395` readings for `5,000` shipments) so the full dataset generates in `<15` seconds and fits comfortably on disk while exercising all excursion, dwell, MKT, and gap rules.
- **GitHub File Size Management**: GitHub enforces a strict limit on individual files $>50\text{ MB}$. Therefore, `data/starter/` (all CSV, JSON, SQLite `.db`, SQL seed, and `ground_truth` files) and `data/full/csv/`, `data/full/ground_truth/`, and `data/full/source_manifest.json` are committed directly, while `python -m src.cli --seed 20261022` deterministically regenerates the complete `data/full/` directory (including `TemperatureReading.json` and `cold_chain_compliance.db`) bit-for-bit on any machine.

---

## 8. Public Data Guidance & Lawful Provenance

- **100% Synthetic Data**: No external proprietary, confidential, or personal data (PII) was collected, scraped, or redistributed.
- **Regulatory Schema Inspiration (Cited for Domain Realism)**:
  - *USP \<1079\> Good Storage and Distribution Practices for Drug Products* (Haynes Mean Kinetic Temperature formula).
  - *EU Good Distribution Practice (GDP) Guidelines (2013/C 343/01)*.
  - *WHO Technical Report Series No. 992, Annex 5* (Transport of time- and temperature-sensitive pharmaceutical products).

---

## 9. Repository Structure

```text
.
├── README.md                          # Complete setup, architecture, reasoning & verification guide
├── docs/
│   ├── DATA_DICTIONARY.md             # Granular Step 1 Data Dictionary & Mermaid ER Diagram
│   ├── VALIDATION_SUMMARY.md          # Step 8 & Step 10 validation metrics & SHA-256 checksum table
│   └── VALIDATION_REPORT.json         # Machine-readable validation & reproducibility report
├── prompts/
│   └── PROMPT_HISTORY.md              # Full prompt history & transparent AI usage disclosure
├── src/
│   ├── __init__.py
│   ├── compliance_engine.py           # MKT (Haynes), excursion, gap, uncertainty & disposition engine
│   ├── generator.py                   # Deterministic Step 1-9 synthetic data generator (seed=20261022)
│   ├── validator.py                   # Step 8 relational/arithmetic/audit validator & Step 10 verifier
│   ├── inspector.py                   # Operations Manager & Analyst CLI shipment/exception inspector
│   └── cli.py                         # End-to-end orchestrator CLI
├── tests/
│   └── test_pipeline.py               # Automated unittest suite verifying all requirements
└── data/
    ├── starter/                       # Complete starter dataset (250 shipments, 100 loggers, CSV/JSON/SQL/GT)
    └── full/                          # Full baseline dataset (5,000 shipments, 1,000 loggers)
```
