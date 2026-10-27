# Validation & Reproducibility Summary Report

- **Fixed Seed**: `20261022`
- **Full Dataset Scale**: `5,000` Shipments, `1,000` Loggers, `10-minute` Telemetry Interval
- **Starter Dataset Scale**: `250` Shipments, `100` Loggers, `10-minute` Telemetry Interval
- **Overall Structural & Referential Validation**: `PASSED (100%)`
- **Two-Run Deterministic Reproducibility (Step 10)**: `PASSED (100% SHA-256 Match)`

---

## 1. Generated Entity Volumes

| Entity / Table Name | Full Scale (`data/full`) Rows | Starter Scale (`data/starter`) Rows | Primary Key Pattern |
| :--- | ---: | ---: | :--- |
| `Product` | `10` | `10` | `PROD-2026-0001` |
| `ThresholdPolicy` | `10` | `10` | `POL-2026-0001` |
| `Packaging` | `10` | `10` | `PKG-2026-0001` |
| `Lot` | `400` | `50` | `LOT-2026-000001` |
| `Logger` | `1,000` | `100` | `LOG-SHP-2026-000001-01` |
| `Shipment` | `5,000` | `250` | `SHP-2026-000001` |
| `ShipmentLot` | `6,000` | `300` | `SHPLOT-2026-000001` |
| `ShipmentLogger` | `4,950` | `247` | `ASSGN-2026-000001` |
| `RouteStop` | `15,000` | `750` | `STOP-SHP-2026-000001-01` |
| `CustodyEvent` | `15,000` | `750` | `CUST-2026-0000001` |
| `TemperatureReading` | `93,395` | `4,666` | `TEMP-LOG-SHP-2026-000001-01-20260102060000` |
| `ExcursionReport` | `5,000` | `250` | `EXCR-2026-000001` |
| `ShipmentStateHistory` | `30,000` | `1,500` | `STHIST-2026-0000001` |
| `GroundTruth` | `5,000` | `250` | `SHP-2026-000001` (Separate File) |

---

## 2. Step 8 Structural, Referential & Business Logic Checks

| Validation Check Category | Status | Verified Metric / Coverage | Error Count |
| :--- | :--- | :--- | ---: |
| Primary Key Uniqueness & Nomenclature | `PASS` | All 13 entity/event tables verified against regex rules | `0` |
| Foreign Key Referential Integrity | `PASS` | `314,700` foreign-key links verified | `0` |
| Allowed Enumerations | `PASS` | `20,020` enum fields verified | `0` |
| Chronological Date & Timestamp Ordering | `PASS` | `26,400` date/time intervals verified | `0` |
| Arithmetic Reconciliation | `PASS` | `5,000` shipments & `15,000` stops reconciled | `0` |
| Lifecycle State Transition History | `PASS` | `5,000` immutable state chains verified | `0` |
| Independent Anomaly & Edge-Case Audit | `PASS` | `5,000 / 5,000` exact ground-truth matches | `0` |

---

## 3. Scenario & Edge-Case Distribution (`5,000` Shipments)

| Scenario Category | Shipment Count | Share (%) | Injected Negative Edge Case? |
| :--- | ---: | ---: | :--- |
| `NORMAL_COMPLIANT` | `3,750` | `75.00%` | No (Standard Operational) |
| `MINOR_EXCURSION` | `300` | `6.00%` | No (Standard Operational) |
| `CRITICAL_EXCURSION` | `200` | `4.00%` | No (Standard Operational) |
| `ROUTE_DWELL_EXCURSION` | `200` | `4.00%` | No (Standard Operational) |
| `CALIBRATION_BORDERLINE` | `150` | `3.00%` | No (Standard Operational) |
| `TELEMETRY_GAP_FLAGGED` | `150` | `3.00%` | No (Standard Operational) |
| `EDGE_CROSS_SHIPMENT_EXCURSION_CALC` | `50` | `1.00%` | YES (Negative Test Case) |
| `EDGE_CUSTODY_GAP_NOT_FLAGGED` | `50` | `1.00%` | YES (Negative Test Case) |
| `EDGE_LOGGER_NOT_ASSIGNED` | `50` | `1.00%` | YES (Negative Test Case) |
| `EDGE_MISSING_DATA_SILENTLY_PASSED` | `50` | `1.00%` | YES (Negative Test Case) |
| `EDGE_PRODUCT_THRESHOLD_MISMATCH` | `50` | `1.00%` | YES (Negative Test Case) |

### Independent Audit Detection of Required Negative Scenarios

| Required Negative Edge Case | Injected Count | Independently Detected Count | Detection Recall |
| :--- | ---: | ---: | ---: |
| `LOGGER_NOT_ASSIGNED` | `50` | `50` | `100.0%` |
| `PRODUCT_THRESHOLD_MISMATCH` | `50` | `50` | `100.0%` |
| `MISSING_DATA_SILENTLY_PASSED` | `50` | `50` | `100.0%` |
| `CUSTODY_GAP_NOT_FLAGGED` | `50` | `50` | `100.0%` |
| `CROSS_SHIPMENT_EXCURSION_CALC` | `50` | `50` | `100.0%` |

### Granular Audit & Decision Accuracy Metrics (Ground-Truth Reconstruction vs. Pre-Audit Reports)

| Evaluation Metric | Measured Value | Interpretation |
| :--- | ---: | :--- |
| **Ground-Truth Reconstruction Accuracy** | `100.00%` (`5,000 / 5,000`) | Independent audit engine reconstructs 100% of true statuses & dispositions from source records |
| **Original Pre-Audit Report Decision Accuracy** | `96.00%` | Reflects the intentionally injected negative process defects in `ExcursionReport` before audit correction |
| **Original Pre-Audit Disposition Accuracy** | `96.00%` | Reflects dispositions prior to independent audit override |
| **Edge-Case Detection Precision** | `100.00%` | Zero false-positive anomaly accusations on normal/operational shipments |
| **Edge-Case Detection Recall** | `100.00%` | 100% of injected negative edge cases (`250 / 250`) caught by independent auditor |
| **False-Compliant Shipments (Pre-Audit)** | `100` | Shipments reported as `COMPLIANT` due to injected defects (`MISSING_DATA_SILENTLY_PASSED`, `CUSTODY_GAP_NOT_FLAGGED`) caught by auditor |
| **Incorrect Excursion Durations (Pre-Audit)** | `100` | Reports with cross-shipment or wrong-policy duration discrepancies caught by auditor |
| **Incorrect Dispositions (Pre-Audit)** | `200` | Reports with defective disposition decisions caught and overridden by auditor |

---

## 4. Compliance Status Distribution (Ground Truth vs. Reported)

| Compliance Status | Ground Truth Count | Reported Count (Before Audit Correction) |
| :--- | ---: | ---: |
| `COMPLIANT` | `3,800` | `3,850` |
| `CRITICAL_EXCURSION` | `400` | `500` |
| `INVESTIGATION_REQUIRED` | `150` | `150` |
| `MINOR_EXCURSION_ACCEPTABLE` | `350` | `300` |
| `NON_COMPLIANT_CUSTODY_GAP` | `50` | `0` |
| `NON_COMPLIANT_MISSING_DATA` | `250` | `200` |

---

## 5. Step 10 Reproducibility Proof (Two Independent Runs with `seed=20261022`)

| Exported File | Row Count (Run 1) | Row Count (Run 2) | SHA-256 Checksum (Run 1 & Run 2) | Match |
| :--- | ---: | ---: | :--- | :--- |
| `csv/CustodyEvent.csv` | `750` | `750` | `77c206d5bf4336bdf4af31f1...` | `IDENTICAL` |
| `csv/ExcursionReport.csv` | `250` | `250` | `cad94a248b125bd8e6172bb4...` | `IDENTICAL` |
| `csv/Logger.csv` | `100` | `100` | `6dce2bc5d610c19215c3599d...` | `IDENTICAL` |
| `csv/Lot.csv` | `50` | `50` | `3ae3382e9956f932b9bbe194...` | `IDENTICAL` |
| `csv/Packaging.csv` | `10` | `10` | `468332636491190a02790020...` | `IDENTICAL` |
| `csv/Product.csv` | `10` | `10` | `9cfb034c5dc479fbefebbf2b...` | `IDENTICAL` |
| `csv/RouteStop.csv` | `750` | `750` | `23a952c8c9dcd575767325f8...` | `IDENTICAL` |
| `csv/Shipment.csv` | `250` | `250` | `6d69b15e1aa4eb60544341a6...` | `IDENTICAL` |
| `csv/ShipmentLogger.csv` | `247` | `247` | `029f0b57422c7767e3b1c844...` | `IDENTICAL` |
| `csv/ShipmentLot.csv` | `300` | `300` | `9ff23e525a0ce2ede651273c...` | `IDENTICAL` |
| `csv/ShipmentStateHistory.csv` | `1,500` | `1,500` | `029cc41b652a1773b7240568...` | `IDENTICAL` |
| `csv/TemperatureReading.csv` | `4,666` | `4,666` | `a0247ad20577de044bfc6d90...` | `IDENTICAL` |
| `csv/ThresholdPolicy.csv` | `10` | `10` | `4fd433bc309a10ee12acce0f...` | `IDENTICAL` |
| `ground_truth/ground_truth.csv` | `250` | `250` | `359d944c25421f3385c16de1...` | `IDENTICAL` |
| `ground_truth/ground_truth.json` | `250` | `250` | `9191f546898f5f41401cacd0...` | `IDENTICAL` |
| `json/CustodyEvent.json` | `750` | `750` | `a9e8a92ddf20d1c388101b8a...` | `IDENTICAL` |
| `json/ExcursionReport.json` | `250` | `250` | `c7ec5d6c2f402de09af49a06...` | `IDENTICAL` |
| `json/Logger.json` | `100` | `100` | `0a59c8f5c832ba77c456c068...` | `IDENTICAL` |
| `json/Lot.json` | `50` | `50` | `b46ce9a962e206c946989110...` | `IDENTICAL` |
| `json/Packaging.json` | `10` | `10` | `a37314fee417ca21fba0ddd9...` | `IDENTICAL` |
| `json/Product.json` | `10` | `10` | `a9fd7414adfd8a35a0d2bc9f...` | `IDENTICAL` |
| `json/RouteStop.json` | `750` | `750` | `1b32c3948a79c59c25b643fd...` | `IDENTICAL` |
| `json/Shipment.json` | `250` | `250` | `c19925c327f97e75d1e048c9...` | `IDENTICAL` |
| `json/ShipmentLogger.json` | `247` | `247` | `1a69358c9f3120a9fc48caa0...` | `IDENTICAL` |
| `json/ShipmentLot.json` | `300` | `300` | `3bf4074bc2daa9d2133735a9...` | `IDENTICAL` |
| `json/ShipmentStateHistory.json` | `1,500` | `1,500` | `d738cec234597730bbfb1f36...` | `IDENTICAL` |
| `json/TemperatureReading.json` | `4,666` | `4,666` | `553eeb1f6e8c14260e908163...` | `IDENTICAL` |
| `json/ThresholdPolicy.json` | `10` | `10` | `343d1b8da9c4f91d7ff07450...` | `IDENTICAL` |
| `sql/seed_data.sql` | `8,893` | `8,893` | `850dea532dbb087c636658e0...` | `IDENTICAL` |
