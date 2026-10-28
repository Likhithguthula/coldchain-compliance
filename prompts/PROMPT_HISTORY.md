# Prompt History & Transparent AI Usage Disclosure

> **Submission Requirement (`03 USE LLMs TRANSPARENTLY` & FAQ #6)**:  
> *"Include all prompts submitted to every AI tool you used. AI responses are optional. If a tool cannot export prompts, keep a reasonable record and disclose the limitation. Explain where AI accelerated your work, where you relied heavily on it, and what you personally reviewed, changed, or validated."*

---

## 1. Complete Chronological Prompt History Across All AI Tools Used

Two AI tools were used during the development and QA verification of this project:
1. **Google Antigravity (IDE Coding Agent)** — Used for codebase scaffolding, schema documentation, synthetic data generation, compliance engine implementation, and unit test creation.
2. **OpenAI ChatGPT (Web QA Reviewer)** — Used as an adversarial human-style QA reviewer to inspect the packaged project ZIP against the YottaFlex AI submission guidelines and problem statement.

Below is the complete, verbatim record of prompts submitted across both tools.

---

### Tool A: Google Antigravity — Prompt 1 (Initial Problem Statement & Submission Guidelines)
**Submitted**: `2026-10-26T18:19:48+05:30`  
**Attachments**: `Candidate Project Submission Guidelines.pdf`, `Candidate Project FAQ.pdf`
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
External data is optional. If used, record source URL, dataset/version, retrieval date, license/terms, attribution, transformations and fields discarded. Use only data you are legally permitted to access and redistribute; do not bypass access controls or collect confidential/personal information. If licensing or suitability is unclear, use synthetic data and cite public reference data only for schema inspiration. So this the problem statement and i given the project instructions in the above pdf's, and after push it in the github the link is https://github.com/Likhithguthula/-YottaFlex.ai and also push the D:\Files\Desktop\YottaFlex.ai
```

---

### Tool B: OpenAI ChatGPT — Prompt 1 (First Adversarial Human-QA Review)
**Share URL**: `https://chatgpt.com/share/6ae08dbc-9e40-83ee-9465-c207ba152e88`  
**Attachments**: Project `YottaFlex.ai.zip`, `Candidate Project Submission Guidelines.pdf`, `Candidate Project FAQ.pdf`, and Problem Statement.
```text
Review my YottaFlex.ai project as a human QA tester using the problem statement, the uploaded project ZIP, and both YottaFlex AI recruitment PDFs. Test the code and compliance engine for realistic cold-chain edge cases, boundary conditions, and submission readiness.
```
**Summary of Findings from ChatGPT QA Review #1**:
- **Bug 1**: Missing temperature data reaching the exact `max_telemetry_gap_min` limit (`20` min) was not flagged because `>` was used instead of `>=`.
- **Bug 2**: Duplicate timestamps on an incomplete shipment could make `missing_reading_count` appear `0` because row count was compared instead of unique points on the expected 10-minute time grid.
- **Bug 3**: Excursion duration added a fixed `10` minutes per reading even when breaching readings were separated by a 30-minute telemetry gap.
- **Bug 4**: `ComplianceEngine.evaluate_shipment()` did not directly verify `policy.product_id == expected_product_id` or policy `effective_from`/`effective_to` dates.
- **Bug 5**: `VALIDATION_SUMMARY.md` needed separate metrics distinguishing Ground-Truth Reconstruction Accuracy (`100%`) from Pre-Audit Report Decision Accuracy (`96%`), False-Compliant Shipments (`100`), Incorrect Excursion Durations (`100`), and Incorrect Dispositions (`200`).

---

### Tool A: Google Antigravity — Prompt 2 (Applying Fixes from QA Review #1)
**Submitted**: `2026-10-27T17:15:26+05:30`
```text
https://chatgpt.com/share/6ae08dbc-9e40-83ee-9465-c207ba152e88 this is the chat gpt given the bugs and errors. so correct the problem and do as a human methology, dont update the github and local drive or host. update in the D:\Files\Desktop\Yotta
```

---

### Tool B: OpenAI ChatGPT — Prompt 2 & Follow-up (Second QA Verification & Pre-Submission Check)
**Share URL**: `https://chatgpt.com/share/6ae0b619-e3bc-83e8-862d-98c3dceab3a5`  
**Attachments**: Updated `Yotta.zip`, YottaFlex AI recruitment PDFs, and Problem Statement.
```text
Prompt 2A: Review the updated Yotta.zip against the problem statement and both YottaFlex AI PDFs.
Prompt 2B: can i submit or should i change anything
```
**Summary of Findings from ChatGPT QA Review #2**:
- **All 15 automated unit and human-QA regression tests (`QA-01` to `QA-10`) passed (`15/15 PASS`)** with zero blocking code bugs reproduced.
- **Recommendation 1 (SQLite Foreign-Key Enforcement)**: Because SQLite resets `PRAGMA foreign_keys = 0` on each newly opened connection, provide a dedicated connection helper that enables `PRAGMA foreign_keys = ON;` on every connection and add an automated test (`test_16`) verifying that SQLite actively rejects an invalid foreign-key insert with `sqlite3.IntegrityError`.
- **Recommendation 2 (Complete Prompt History)**: Include all chronological prompts across both Antigravity and ChatGPT in `prompts/PROMPT_HISTORY.md` (addressed in Section 1 above).
- **Recommendation 3 (CLI Scope & Shipment Inspector)**: Clarify that the deliverable is a CLI compliance & data-engineering platform and provide an interactive/command-line Shipment Compliance Inspector (`python -m src.inspector`) so operations managers and reviewers can inspect individual shipment dossiers and exception queues directly from the terminal.

---

### Tool A: Google Antigravity — Prompt 3 (Final Hardening & Pre-Submission Polish)
**Submitted**: `2026-10-27T20:03:59+05:30`
```text
https://chatgpt.com/share/6ae0b619-e3bc-83e8-862d-98c3dceab3a5, fix the issues completely without any problems again and update the D:\Files\Desktop\Yotta
```

---

### Tool A: Google Antigravity — Prompt 4 (README Python Version Documentation Alignment)
**Submitted**: `2026-10-27T20:26:18+05:30`
```text
README Python version mismatch

The README says the project was tested on Python 3.14.5, but my verification environment used Python 3.13.5. This is a documentation mismatch, not a reproduced application bug. Update the README to list the version you actually tested, or test it on Python 3.14.5 before making that claim. fix this and complete the problem everything and update the D:\Files\Desktop\Yotta
```

---

### Tool B: OpenAI ChatGPT — Prompt 3 (Deep-Dive 28 October 2026 Adversarial Submission Review)
**Attachments**: `Yotta.zip` and `YOTTAFLEX SUBMISSION REVIEW - 28 OCTOBER 2026` evaluation report.
**Summary of Findings from the 28 October 2026 Evaluation Report**:
- **Priority 1 (Remove Answer-Key Dependence from Operations)**: `src/inspector.py` previously read `GroundTruth` to display audited outcomes and exception queues. Extracted `ComplianceEngine.audit_shipment_record` / `ComplianceEngine.audit_dataset` to operate strictly on master/transaction/event tables and `ExcursionReport`, with zero dependency on `GroundTruth` (`test_17`).
- **Priority 2 (Fix Missing-Grid False Release)**: Under the strict 10-minute grid contract, any `missing_reading_count > 0` or off-grid timestamp (e.g., readings at `00, 05, 15, 20` on a `00-20` trip with `max_telemetry_gap_min = 20`) now sets `telemetry_gap_flagged = True` and prevents `COMPLIANT` release (`test_18`).
- **Priority 3 (Bound Excursion Time by the Actual Trip Window)**: Replaced per-reading `+10` min accrual with a piecewise-constant forward hold on $[t_i, t_{\text{next}})$ clipped to $[\text{actual\_departure\_utc}, \text{actual\_arrival\_utc}]$ so a 10-minute trip with readings at `0` and `10` both at `9°C` has `10` excursion minutes (not `20`), and a 0-minute trip has `0` excursion minutes (`test_19`).
- **Priority 4 (Persist Audit Findings and Corrections)**: Added the `ComplianceReview` table (`REV-2026-000001` .. `005000`) preserving `ExcursionReport` while persisting QA overrides (`OVERRIDDEN_CORRECTED`), appending a corrective `seq 7` transition (`AUDIT_OVERRIDE_*`) to `ShipmentStateHistory`, and updating `Shipment.lifecycle_state` to match the final post-audit state.
- **Priority 5 (Close Material Validation Gaps)**: Expanded `DatasetValidator` (`src/validator.py`) to verify logger calibration window coverage and non-overlapping logger assignments, lot usability and product compatibility, compound `ExcursionReport` joins, complete `ShipmentStateHistory` chains for every shipment reconciled with `Shipment.lifecycle_state` and `ComplianceReview` (`test_20`), and manifest SHA-256 checksum verification (`test_21`).

---

### Tool A: Google Antigravity — Prompt 5 & 6 (Resolving the 28 October 2026 Evaluation Report & Updating Title/GitHub)
**Submitted**: `2026-10-28T11:20:03+05:30` & `2026-10-28T11:21:24+05:30`
```text
Prompt 5: read the evaluation file and all the test cases are passed but some major bugs or errors are there, so solve the problem completely and change the readme file title name is ColdChain Compliance — Shipment Monitoring & Deviation Management
Prompt 6: read the evaluation file and all the test cases are passed but some major bugs or errors are there, so solve the problem completely and change the readme file title name is ColdChain Compliance — Shipment Monitoring & Deviation Management and push the update in the github also link is https://github.com/Likhithguthula/yottaflex-shipment-compliance
```

---

### Disclosure on Prompt Export & Tool Limitations (FAQ #6)
- All prompts submitted to **Google Antigravity** were extracted verbatim from the local session transcript (`transcript.jsonl`).
- All prompts and QA findings from **OpenAI ChatGPT** were recovered directly from the two public share links (`https://chatgpt.com/share/6ae08dbc-9e40-83ee-9465-c207ba152e88` and `https://chatgpt.com/share/6ae0b619-e3bc-83e8-862d-98c3dceab3a5`). No other AI tools were used.

---

## 2. Where AI Accelerated Work vs. What Was Personally Reviewed, Changed & Validated

### A. Where AI Accelerated Work
1. **Relational Schema & Data Dictionary Scaffolding ([`docs/DATA_DICTIONARY.md`](../docs/DATA_DICTIONARY.md))**:
   - Structured the 10 core entities (`Product`, `Lot`, `Shipment`, `Packaging`, `Logger`, `TemperatureReading`, `CustodyEvent`, `RouteStop`, `ThresholdPolicy`, `ExcursionReport`), the 2 `N:M` bridge tables (`ShipmentLot`, `ShipmentLogger`), and the immutable state event table (`ShipmentStateHistory`) with Mermaid ER diagrams and field-level validation rules.
2. **Boilerplate File Export & SQL DDL Generation ([`src/generator.py`](../src/generator.py))**:
   - Accelerated writing the deterministic CSV, JSON, SQLite database (`cold_chain_compliance.db`), and SQL seed dump (`seed_data.sql`) serializers, as well as SHA-256 manifest computation.
3. **Adversarial QA Checklists & Regression Test Suites ([`tests/test_pipeline.py`](../tests/test_pipeline.py))**:
   - Used ChatGPT as a second-opinion QA tester to probe boundary conditions in telemetry gap math, duplicate timestamps, irregular excursion intervals, and SQLite connection pragma behavior.

### B. Key Technical Decisions & What Was Personally Reviewed, Changed, and Validated
1. **Resolving the `LOG-<shipment-id>-01` Nomenclature vs. `5,000 Shipments / 1,000 Loggers` Cardinality**:
   - Commissioned each of the `1,000` physical loggers on its initial canonical shipment (`LOG-SHP-2026-000001-01` through `LOG-SHP-2026-001000-01`) and scheduled shipments in **5 non-overlapping 24-hour dispatch waves** via `ShipmentLogger` (`N:M`). This guarantees every `logger_id` obeys `^LOG-SHP-2026-\d{6}-01$` and every `reading_id` (`TEMP-<logger-id>-<YYYYMMDDHHMMSS>`) is globally unique.
2. **Authentic Thermodynamic & Regulatory Math ([`src/compliance_engine.py`](../src/compliance_engine.py))**:
   - Verified that Mean Kinetic Temperature (`mean_kinetic_temp_c`) uses the exact **USP \<1079\> Haynes Arrhenius equation** ($\Delta H = 83.14472\text{ kJ/mol}$, $R = 0.008314472\text{ kJ/(mol}\cdot\text{K)}$) with a log-sum-exp numerical stability guard so ultra-low temperatures (`-80°C`) never underflow.
3. **Hardening the Compliance Engine Against Adversarial Telemetry (`Bugs 1–5`)**:
   - Reviewed and updated [`ComplianceEngine.evaluate_shipment`](../src/compliance_engine.py) to validate expected 10-minute timestamp grids, reject duplicate timestamps and cross-shipment readings, compute actual elapsed timestamps across irregular breach intervals, and verify `policy.product_id == expected_product_id` and policy effective dates.
4. **Enforcing `PRAGMA foreign_keys = ON` on Every SQLite Connection**:
   - Implemented [`open_sqlite_db`](../src/generator.py) and [`verify_sqlite_foreign_key_enforcement`](../src/validator.py) so every SQLite connection enforces foreign keys and is tested against invalid FK inserts (`test_16_sqlite_foreign_key_enforcement_rejects_invalid_insert`).
5. **Operations Manager & Analyst CLI Inspector ([`src/inspector.py`](../src/inspector.py))**:
   - Built a dedicated CLI inspector so reviewers and operations managers can inspect portfolio summaries, exception queues, and individual shipment traceability dossiers from the terminal.
