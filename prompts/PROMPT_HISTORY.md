# Prompt History & Transparent AI Usage Disclosure

> **Submission Requirement (`03 USE LLMs TRANSPARENTLY`)**:  
> *"LLM tools are allowed. Submit your prompt history and explain where AI accelerated your work, where you relied heavily on it, and what you personally reviewed, changed, or validated."*

---

## 1. Prompts Submitted to AI Tools

### Prompt 1 — Full Problem Statement & Submission Guidelines
```text
Business Context / Problem
Prove that temperature-sensitive shipments remained within product and route requirements, and manage deviations.

Objective and Scope
Build a working solution for this end-to-end workflow: Prove that temperature-sensitive shipments remained within product and route requirements, and manage deviations. The solution should expose key decisions and exceptions, preserve traceability, and produce measurable outputs. Medium complexity means multiple related entities, conditional rules, at least one exception path and meaningful evaluation metrics.

Primary Actors / Systems
Supplier, planner, carrier/warehouse operator, logistics analyst and operations manager

Business Workflow and Rules
1. Generate/reference master data first: Product, Lot, Shipment, Packaging, Logger.
2. Generate dependent transaction/event records using valid parent IDs.
3. Apply business logic: Create product/lot requirements and packaging profiles; assign calibrated loggers to shipments; generate timestamped temperature readings and custody handoffs; define product-specific thresholds and allowed durations; inject controlled excursions and missing data; create compliance reports.
4. Persist state changes as explicit records/events where relevant; do not overwrite history.
5. Generate outcomes and ground-truth labels from documented rules, not independent random assignment.

Entities and Relationships
Core entities: Product, Lot, Shipment, Packaging, Logger, TemperatureReading, CustodyEvent, RouteStop, ThresholdPolicy, ExcursionReport. Define a primary key for every entity and foreign keys for every relationship. Document cardinality (1:1, 1:N, N:M), optional relationships, effective dates and lifecycle states. Use bridge tables for many-to-many relationships; retain event history for state changes; validate all joins. For deliberate anomaly cases, keep an issue_type/ground_truth label rather than silently corrupting unrelated rows.

ID / Nomenclature Conventions
LOT-2026-000001; SHP-2026-000001; LOG-<shipment-id>-01; TEMP-<logger-id>-<timestamp>; EXCR-2026-000001.

Synthetic Data Generation Instructions (Granular)
Step 1 — Write a data dictionary for: Product, Lot, Shipment, Packaging, Logger, TemperatureReading, CustodyEvent, RouteStop, ThresholdPolicy, ExcursionReport. For each field specify type, required/optional, allowed values, units, uniqueness and validation rule.
Step 2 — Create parent/master tables first, then child and event tables. Apply this domain-specific generation logic: Create product/lot requirements and packaging profiles; assign calibrated loggers to shipments; generate timestamped temperature readings and custody handoffs; define product-specific thresholds and allowed durations; inject controlled excursions and missing data; create compliance reports.
Step 3 — Apply deterministic IDs and naming: LOT-2026-000001; SHP-2026-000001; LOG-<shipment-id>-01; TEMP-<logger-id>-<timestamp>; EXCR-2026-000001. Keep counters zero-padded, IDs immutable, and referenced IDs exactly consistent across files.
Step 4 — Generate the baseline volume: Generate 5,000 shipments, 1,000 loggers and readings every 10 minutes. Include normal trips, excursions, calibration uncertainty, logger gaps and route dwell events. Use a fixed seed (e.g. 20261022) and parameterize scale so starter and full datasets use the same generator.
Step 5 — Generate valid baseline records first, then inject labelled edge cases: Logger not assigned to shipment; product threshold mismatch; missing data silently passed; custody gap not flagged; excursion duration computed across unrelated shipments.
Step 6 — Calculate derived values (totals, balances, scores, statuses and dates) from source records rather than generating them independently.
Step 7 — Produce a separate ground_truth.csv/json for known anomalies, expected decisions or labels; never leak it into input features.
Step 8 — Run validation: primary-key uniqueness, foreign-key integrity, allowed enums, date ordering, arithmetic reconciliation, lifecycle transitions and duplicate checks.
Step 9 — Export CSV/JSON/SQL seed data plus README, schema/data dictionary, generator command, fixed seed, source manifest and validation summary.
Step 10 — Regenerate twice with the same seed and compare row counts/checksums to demonstrate reproducibility.

Suggested Volume and Coverage
Generate 5,000 shipments, 1,000 loggers and readings every 10 minutes. Include normal trips, excursions, calibration uncertainty, logger gaps and route dwell events.

Required Edge Cases / Negative Scenarios
Logger not assigned to shipment; product threshold mismatch; missing data silently passed; custody gap not flagged; excursion duration computed across unrelated shipments.

Validation Rules / Acceptance Criteria
Compliance report traces each conclusion to readings and policy; gaps are flagged; excursion duration is reproducible; lot and shipment links remain intact.

Evaluation Focus for Generated Data
Review: (a) realistic distributions and meaningful variation; (b) consistent IDs and valid joins; (c) dependency/lifecycle consistency across entities; (d) correct derived values and reconciliation; (e) normal, boundary, failure and exception coverage; (f) separate ground truth; (g) fixed-seed reproducibility, documentation and lawful provenance.

Public Data Guidance / Lawful Use
External data is optional. If used, record source URL, dataset/version, retrieval date, license/terms, attribution, transformations and fields discarded. Use only data you are legally permitted to access and redistribute; do not bypass access controls or collect confidential/personal information. If licensing or suitability is unclear, use synthetic data and cite public reference data only for schema inspiration.
```
*(Accompanied by the two YottaFlex AI Recruitment Drive PDFs: `Candidate Project Submission Guidelines` and `Candidate Project FAQ`).*

---

## 2. How AI Accelerated the Work vs. What Was Reviewed & Validated

### A. Where AI Accelerated Work
1. **Relational Schema & Data Dictionary Scaffolding ([`docs/DATA_DICTIONARY.md`](../docs/DATA_DICTIONARY.md))**:
   - Rapidly structured the 10 core entities (`Product`, `Lot`, `Shipment`, `Packaging`, `Logger`, `TemperatureReading`, `CustodyEvent`, `RouteStop`, `ThresholdPolicy`, `ExcursionReport`) plus the 2 required `N:M` bridge tables (`ShipmentLot`, `ShipmentLogger`) and the immutable state event table (`ShipmentStateHistory`) with Mermaid ER diagrams and field-level validation rules.
2. **Boilerplate File Export & SQL DDL Generation ([`src/generator.py`](../src/generator.py))**:
   - Accelerated writing the deterministic CSV, JSON, SQLite database (`cold_chain_compliance.db`), and SQL seed dump (`seed_data.sql`) serializers, as well as SHA-256 manifest computation.
3. **Validation Harness & Unit Test Structure ([`src/validator.py`](../src/validator.py), [`tests/test_pipeline.py`](../tests/test_pipeline.py))**:
   - Generated the structural checks for primary-key regex compliance, foreign-key referential integrity across 19 relationships, enum enforcement, chronological ordering, and Step 10 two-run reproducibility comparisons.

### B. Key Technical Decisions & What Was Personally Reviewed, Changed, and Validated
1. **Resolving the `LOG-<shipment-id>-01` Nomenclature vs. `5,000 Shipments / 1,000 Loggers` Cardinality**:
   - *Design Review*: The problem requires `5,000` shipments and `1,000` loggers (a `5:1` reuse ratio across shipments) while specifying the logger ID convention `LOG-<shipment-id>-01`.
   - *Engineered Solution*: We commissioned each of the `1,000` physical loggers on its initial canonical shipment (`LOG-SHP-2026-000001-01` through `LOG-SHP-2026-001000-01`) and scheduled shipments in **5 non-overlapping 24-hour dispatch waves** via the `ShipmentLogger` (`N:M`) bridge table. This guarantees that:
     - Every `logger_id` strictly obeys `^LOG-SHP-2026-\d{6}-01$`.
     - Every `reading_id` (`TEMP-<logger-id>-<YYYYMMDDHHMMSS>`) is 100% globally unique because no logger is ever active on two shipments at the same timestamp.
2. **Authentic Thermodynamic & Regulatory Math ([`src/compliance_engine.py`](../src/compliance_engine.py))**:
   - Verified that Mean Kinetic Temperature (`mean_kinetic_temp_c`) is not approximated as a simple average, but computed using the exact **USP \<1079\> Haynes Arrhenius equation** ($\Delta H = 83.14472\text{ kJ/mol}$, $R = 0.008314472\text{ kJ/(mol}\cdot\text{K)}$) with a log-sum-exp numerical stability guard so ultra-low temperatures (`-80°C`) never underflow.
3. **Strict Isolation of Ground Truth & Exact Modeling of the 5 Negative Edge Cases**:
   - Verified that input tables (`Shipment`, `ExcursionReport`, `CustodyEvent`, `TemperatureReading`, `ShipmentLogger`) contain **zero ground-truth labels** (`scenario_category`, `edge_case_type`, `true_compliance_status`).
   - Reviewed and validated that each of the 5 required negative scenarios (`LOGGER_NOT_ASSIGNED`, `PRODUCT_THRESHOLD_MISMATCH`, `MISSING_DATA_SILENTLY_PASSED`, `CUSTODY_GAP_NOT_FLAGGED`, `CROSS_SHIPMENT_EXCURSION_CALC`) arises from realistic relational/process anomalies and is **100% recoverable** by the independent auditor in [`src/validator.py`](../src/validator.py).
4. **Bug Fix & End-to-End Verification**:
   - Caught and fixed a variable reference typo (`custody_events_spec` -> `cust_events_spec`) in [`src/generator.py`](../src/generator.py) during test execution, then verified 100% pass rates across all unit tests and Step 10 reproducibility checks.
5. **Adversarial Human-QA Review & Hardening ([`src/compliance_engine.py`](../src/compliance_engine.py), [`src/validator.py`](../src/validator.py), [`tests/test_pipeline.py`](../tests/test_pipeline.py))**:
   - Conducted an adversarial QA review to identify and fix 5 boundary/telemetry edge cases:
     1. **Exact Telemetry Gap Boundary (`>= max_telemetry_gap_min`)**: Updated gap comparison so a missing-data window reaching the policy limit is flagged, and any missing points on the expected time grid prevent silent compliance approval.
     2. **Duplicate Timestamps & Expected Time-Grid Coverage**: Added duplicate `reading_id` and per-logger timestamp detection, calculating `missing_reading_count` against the explicit 10-minute expected timestamp grid rather than raw row counts.
     3. **Irregular-Interval Excursion Duration & Uncertainty**: Replaced fixed 10-minute increments when breaches span across missing-data gaps with actual elapsed timestamp duration and automatic uncertainty flagging.
     4. **Product-to-Policy Identity & Effective-Date Validation**: Added `expected_product_id`, `effective_from`/`effective_to`, and `expected_shipment_id` checks directly inside `ComplianceEngine.evaluate_shipment()`.
     5. **Separate Audit vs. Pre-Audit Report Accuracy Metrics**: Published distinct metrics in `VALIDATION_SUMMARY.md` and `VALIDATION_REPORT.json` for Ground-Truth Reconstruction Accuracy (`100.0%`), Original Pre-Audit Report Decision Accuracy (`95.0%`), Edge-Case Precision/Recall (`100.0%`), False-Compliant Shipments (`100`), Incorrect Excursion Durations (`100`), and Incorrect Dispositions (`250`), backed by 15 automated unit/regression tests covering `QA-01` through `QA-10`.
