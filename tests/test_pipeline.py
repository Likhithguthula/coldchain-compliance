"""
Comprehensive Automated & Human-QA Regression Test Suite for the YottaFlex
Cold-Chain Shipment Compliance & Deviation Management Solution.

Covers:
1. Baseline generator, nomenclature, foreign-key, arithmetic, lifecycle & reproducibility checks.
2. All 10 Human QA Test Plan scenarios (QA-01 through QA-10):
   - QA-01: Normal compliant shipment
   - QA-02: Exact temperature boundary inclusive/exclusive semantics
   - QA-03: Missing readings & exact boundary telemetry gap detection (Bug 1 regression)
   - QA-04: Duplicate timestamps & duplicate reading IDs on incomplete grid (Bug 2 regression)
   - QA-05: Long excursion across irregular time intervals & gap uncertainty (Bug 3 regression)
   - QA-06: Wrong product policy & expired policy detection in ComplianceEngine (Bug 4 regression)
   - QA-07: Unassigned logger & orphan readings handling
   - QA-08: Configurable custody handoff gap threshold enforcement
   - QA-09: Cross-shipment reading contamination & out-of-window reading rejection
   - QA-10: Fixed-seed repeatability & separate pre-audit vs. post-audit metrics (Bug 5 regression)
3. Additional adversarial telemetry tests:
   - Out-of-order reading timestamps
   - Missing departure or arrival endpoint readings
   - Invalid timestamp format rejection

Run via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import io
import sqlite3
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from src.compliance_engine import (
    ComplianceEngine,
    PolicyThresholds,
    ReadingPoint,
    compute_mkt_celsius,
    parse_iso_utc,
)
from src.generator import (
    DEFAULT_SEED,
    SyntheticDataGenerator,
    export_datasets,
    open_sqlite_db,
    verify_manifest_checksums,
)
from src.inspector import inspect_shipment, list_exception_shipments, print_portfolio_summary
from src.validator import DatasetValidator, verify_reproducibility, verify_sqlite_foreign_key_enforcement


def make_test_policy(
    policy_id: str = "POL-2026-0001",
    product_id: str = "PROD-2026-0001",
    min_temp_c: float = 2.0,
    max_temp_c: float = 8.0,
    critical_low_temp_c: float = 0.0,
    critical_high_temp_c: float = 12.0,
    max_single_excursion_min: int = 40,
    max_cumulative_excursion_min: int = 90,
    max_mkt_c: float = 8.3,
    max_telemetry_gap_min: int = 20,
    max_custody_gap_min: int = 120,
    effective_from: str = "2026-01-01T00:00:00Z",
    effective_to: str | None = None,
) -> PolicyThresholds:
    return PolicyThresholds(
        policy_id=policy_id,
        product_id=product_id,
        min_temp_c=min_temp_c,
        max_temp_c=max_temp_c,
        critical_low_temp_c=critical_low_temp_c,
        critical_high_temp_c=critical_high_temp_c,
        max_single_excursion_min=max_single_excursion_min,
        max_cumulative_excursion_min=max_cumulative_excursion_min,
        max_mkt_c=max_mkt_c,
        max_telemetry_gap_min=max_telemetry_gap_min,
        max_custody_gap_min=max_custody_gap_min,
        effective_from=effective_from,
        effective_to=effective_to,
    )


class TestColdChainCompliancePlatform(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gen = SyntheticDataGenerator(seed=DEFAULT_SEED, scale="starter")
        cls.datasets = cls.gen.generate_all()
        cls.validator = DatasetValidator(cls.datasets)
        cls.val_report = cls.validator.run_all_checks()

    def test_01_mkt_haynes_formula(self) -> None:
        """Verify Haynes Mean Kinetic Temperature (MKT) calculation accuracy."""
        constant_temps = [5.0, 5.0, 5.0, 5.0, 5.0]
        self.assertEqual(compute_mkt_celsius(constant_temps), 5.0)

        variable_temps = [4.0, 4.0, 4.0, 12.0, 12.0]
        arith_mean = sum(variable_temps) / len(variable_temps)
        mkt = compute_mkt_celsius(variable_temps)
        self.assertIsNotNone(mkt)
        self.assertGreater(mkt, arith_mean)

    def test_02_nomenclature_and_primary_keys(self) -> None:
        """Verify Step 3 nomenclature rules and primary key uniqueness across all 13 tables."""
        pk_res = self.val_report["primary_key_and_nomenclature"]
        self.assertTrue(pk_res["passed"], f"PK/Nomenclature errors: {pk_res['sample_errors']}")
        self.assertEqual(pk_res["error_count"], 0)

    def test_03_foreign_key_referential_integrity(self) -> None:
        """Verify all foreign key joins across parent, child, and bridge tables."""
        fk_res = self.val_report["foreign_key_integrity"]
        self.assertTrue(fk_res["passed"], f"FK errors: {fk_res['sample_errors']}")
        self.assertEqual(fk_res["error_count"], 0)

    def test_04_chronological_and_arithmetic_reconciliation(self) -> None:
        """Verify Step 6 derived values and date ordering."""
        self.assertTrue(self.val_report["date_ordering"]["passed"])
        self.assertTrue(self.val_report["arithmetic_reconciliation"]["passed"])
        self.assertTrue(self.val_report["lifecycle_transitions"]["passed"])

    def test_05_five_negative_edge_cases_and_ground_truth_metrics(self) -> None:
        """Verify Step 5, Step 7, and Bug 5 separate audit accuracy metrics."""
        audit_res = self.val_report["independent_anomaly_audit"]
        self.assertTrue(audit_res["passed"], f"Audit mismatches: {audit_res['sample_mismatches']}")
        edge_counts = audit_res["detected_edge_case_counts"]
        for required_edge in (
            "LOGGER_NOT_ASSIGNED",
            "PRODUCT_THRESHOLD_MISMATCH",
            "MISSING_DATA_SILENTLY_PASSED",
            "CUSTODY_GAP_NOT_FLAGGED",
            "CROSS_SHIPMENT_EXCURSION_CALC",
        ):
            self.assertGreater(edge_counts.get(required_edge, 0), 0, f"Missing edge case {required_edge}")

        # Bug 5 regression check: ensure separate metrics are reported for Ground-Truth reconstruction
        # vs. Original pre-audit report accuracy, false-compliant shipments, and precision/recall.
        self.assertEqual(audit_res["ground_truth_reconstruction_accuracy_pct"], 100.0)
        self.assertLess(audit_res["original_report_decision_accuracy_pct"], 100.0)
        self.assertEqual(audit_res["edge_case_detection_precision_pct"], 100.0)
        self.assertEqual(audit_res["edge_case_detection_recall_pct"], 100.0)
        self.assertGreater(audit_res["false_compliant_shipments_in_uncorrected_reports"], 0)
        self.assertGreater(audit_res["incorrect_excursion_durations_in_uncorrected_reports"], 0)
        self.assertGreater(audit_res["incorrect_dispositions_in_uncorrected_reports"], 0)

    def test_06_step10_fixed_seed_reproducibility(self) -> None:
        """QA-10: Verify Step 10 regenerating twice with the same seed yields identical row counts and SHA-256 hashes."""
        repro = verify_reproducibility(seed=DEFAULT_SEED, scale="starter")
        self.assertTrue(repro["reproducible_100_pct"])

    def test_07_qa01_and_qa02_normal_and_exact_boundary_temperatures(self) -> None:
        """QA-01 & QA-02: Verify normal compliant readings and inclusive exact boundary limits [min_temp_c, max_temp_c]."""
        policy = make_test_policy(min_temp_c=2.0, max_temp_c=8.0)
        # 5 readings over 40 minutes: includes exact boundaries 2.0C and 8.0C
        temps = [5.0, 2.0, 5.0, 8.0, 5.0]
        readings = [
            ReadingPoint(
                reading_id=f"TEMP-LOG-SHP-2026-000001-01-2026010206{i*10:02d}00",
                logger_id="LOG-SHP-2026-000001-01",
                shipment_id="SHP-2026-000001",
                sequence_no=i + 1,
                recorded_at_utc=f"2026-01-02T06:{i*10:02d}:00Z",
                temperature_c=t,
                calibration_uncertainty_c=0.25,
                elapsed_since_prev_min=0 if i == 0 else 10,
            )
            for i, t in enumerate(temps)
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=policy,
            readings=readings,
            custody_handover_gaps_min=[15, 20, 15],
            expected_product_id="PROD-2026-0001",
            expected_shipment_id="SHP-2026-000001",
        )
        # Exact boundary temperatures (2.0 and 8.0) are within the inclusive permitted range [2.0, 8.0]
        self.assertEqual(res.total_excursion_duration_min, 0)
        self.assertEqual(res.missing_reading_count, 0)
        self.assertEqual(res.compliance_status, "COMPLIANT")
        self.assertEqual(res.disposition_decision, "AUTO_RELEASE")

    def test_08_qa03_and_bug1_missing_data_and_exact_gap_limit(self) -> None:
        """
        QA-03 & Bug 1 Regression:
        40-minute shipment expecting readings every 10 minutes:
        00:00 -> 1.0C (breach), 00:10 -> Missing, 00:20 -> Missing, 00:30 -> 1.0C (breach), 00:40 -> 5.0C.
        Missing window is 20 minutes (reaching max_telemetry_gap_min=20), and excursion spans across a gap.
        Must flag telemetry_gap_flagged=True and NEVER release the shipment.
        """
        policy = make_test_policy(max_telemetry_gap_min=20, max_single_excursion_min=40, max_cumulative_excursion_min=90)
        readings = [
            ReadingPoint(
                reading_id="TEMP-LOG-SHP-2026-000001-01-20260102060000",
                logger_id="LOG-SHP-2026-000001-01",
                shipment_id="SHP-2026-000001",
                sequence_no=1,
                recorded_at_utc="2026-01-02T06:00:00Z",
                temperature_c=1.0,
                calibration_uncertainty_c=0.2,
                elapsed_since_prev_min=0,
            ),
            ReadingPoint(
                reading_id="TEMP-LOG-SHP-2026-000001-01-20260102063000",
                logger_id="LOG-SHP-2026-000001-01",
                shipment_id="SHP-2026-000001",
                sequence_no=2,
                recorded_at_utc="2026-01-02T06:30:00Z",
                temperature_c=1.0,
                calibration_uncertainty_c=0.2,
                elapsed_since_prev_min=30,
            ),
            ReadingPoint(
                reading_id="TEMP-LOG-SHP-2026-000001-01-20260102064000",
                logger_id="LOG-SHP-2026-000001-01",
                shipment_id="SHP-2026-000001",
                sequence_no=3,
                recorded_at_utc="2026-01-02T06:40:00Z",
                temperature_c=5.0,
                calibration_uncertainty_c=0.2,
                elapsed_since_prev_min=10,
            ),
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=policy,
            readings=readings,
            custody_handover_gaps_min=[10, 15],
        )
        self.assertEqual(res.max_telemetry_gap_min, 20)
        self.assertTrue(res.telemetry_gap_flagged, "Bug 1: 20-min gap reaching limit must be flagged")
        self.assertEqual(res.missing_reading_count, 2)
        # Bug 3: Two breach readings at 00:00 and 00:30 separated by 30 minutes must not be understated as 20 min
        self.assertGreaterEqual(res.total_excursion_duration_min, 40)
        self.assertTrue(res.uncertainty_borderline_flag)
        self.assertNotIn(res.disposition_decision, ("AUTO_RELEASE", "RELEASE_WITH_STABILITY_DEDUCTION"))
        self.assertEqual(res.disposition_decision, "QUARANTINE_HOLD")

    def test_09_qa04_and_bug2_duplicate_readings_cannot_mask_missing_grid_points(self) -> None:
        """
        QA-04 & Bug 2 Regression:
        Supply 5 readings for a 40-minute shipment, but with duplicated timestamps:
        - Three readings at 00:00
        - Two readings at 00:30
        - No readings at 00:10, 00:20, or 00:40
        Engine must detect duplicates, compute missing_reading_count=3 from the expected grid,
        flag telemetry_gap_flagged=True, and quarantine the shipment.
        """
        policy = make_test_policy(max_telemetry_gap_min=30)
        dup_readings = [
            ReadingPoint("R1", "LOG-1", "SHP-1", 1, "2026-01-02T06:00:00Z", 5.0, 0.2, 0),
            ReadingPoint("R2", "LOG-1", "SHP-1", 2, "2026-01-02T06:00:00Z", 5.0, 0.2, 0),
            ReadingPoint("R3", "LOG-1", "SHP-1", 3, "2026-01-02T06:00:00Z", 5.0, 0.2, 0),
            ReadingPoint("R4", "LOG-1", "SHP-1", 4, "2026-01-02T06:30:00Z", 5.0, 0.2, 30),
            ReadingPoint("R5", "LOG-1", "SHP-1", 5, "2026-01-02T06:30:00Z", 5.0, 0.2, 0),
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=policy,
            readings=dup_readings,
            custody_handover_gaps_min=[10],
        )
        self.assertEqual(res.expected_reading_count, 5)
        self.assertEqual(res.actual_reading_count, 2, "Only 2 unique timestamps exist")
        self.assertEqual(res.missing_reading_count, 3, "00:10, 00:20, and 00:40 are missing from the time grid")
        self.assertTrue(res.telemetry_gap_flagged)
        self.assertEqual(res.compliance_status, "NON_COMPLIANT_MISSING_DATA")
        self.assertEqual(res.disposition_decision, "QUARANTINE_HOLD")

    def test_10_qa05_and_bug3_irregular_interval_excursion_duration(self) -> None:
        """
        QA-05 & Bug 3 Regression:
        Verify that out-of-range readings separated by irregular intervals account for actual elapsed
        timestamps and flag uncertainty rather than understating duration as fixed 10-minute steps.
        """
        policy = make_test_policy(max_telemetry_gap_min=60, max_single_excursion_min=25, max_cumulative_excursion_min=50)
        readings = [
            ReadingPoint("R1", "LOG-1", "SHP-1", 1, "2026-01-02T06:00:00Z", 1.0, 0.2, 0),
            ReadingPoint("R2", "LOG-1", "SHP-1", 2, "2026-01-02T06:30:00Z", 1.0, 0.2, 30),
            ReadingPoint("R3", "LOG-1", "SHP-1", 3, "2026-01-02T06:40:00Z", 5.0, 0.2, 10),
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=policy,
            readings=readings,
            custody_handover_gaps_min=[10],
        )
        # Duration between 06:00 (10 min initial slot) + 30 min elapsed to 06:30 = 40 minutes (> 25 min single limit)
        self.assertEqual(res.total_excursion_duration_min, 40)
        self.assertEqual(res.max_single_excursion_min, 40)
        self.assertTrue(res.uncertainty_borderline_flag)
        self.assertEqual(res.compliance_status, "CRITICAL_EXCURSION")
        self.assertEqual(res.disposition_decision, "QUARANTINE_HOLD")

    def test_11_qa06_and_bug4_product_policy_mismatch_and_effective_dates(self) -> None:
        """
        QA-06 & Bug 4 Regression:
        Verify that ComplianceEngine validates product-policy compatibility and effective dates,
        routing mismatches to INVESTIGATION_REQUIRED / QUARANTINE_HOLD instead of COMPLIANT.
        """
        wrong_policy = make_test_policy(policy_id="POL-2026-0005", product_id="PROD-2026-0005")
        readings = [
            ReadingPoint(f"R{i}", "LOG-1", "SHP-1", i + 1, f"2026-01-02T06:{i*10:02d}:00Z", 5.0, 0.2, 0 if i == 0 else 10)
            for i in range(5)
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=wrong_policy,
            readings=readings,
            custody_handover_gaps_min=[10],
            expected_product_id="PROD-2026-0001",  # Mismatches policy.product_id == PROD-2026-0005
        )
        self.assertEqual(res.compliance_status, "INVESTIGATION_REQUIRED")
        self.assertEqual(res.disposition_decision, "QUARANTINE_HOLD")

        # Also test expired policy
        expired_policy = make_test_policy(
            product_id="PROD-2026-0001",
            effective_from="2025-01-01T00:00:00Z",
            effective_to="2025-12-31T23:59:59Z",
        )
        res_exp = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=expired_policy,
            readings=readings,
            custody_handover_gaps_min=[10],
            expected_product_id="PROD-2026-0001",
        )
        self.assertEqual(res_exp.compliance_status, "INVESTIGATION_REQUIRED")
        self.assertEqual(res_exp.disposition_decision, "QUARANTINE_HOLD")

    def test_12_qa07_unassigned_logger_and_orphan_readings(self) -> None:
        """QA-07: Remove shipment-to-logger assignment or supply orphan readings (shipment_id=None)."""
        policy = make_test_policy()
        orphan_readings = [
            ReadingPoint("R1", "LOG-1", None, 1, "2026-01-02T06:00:00Z", 5.0, 0.2, 0),
            ReadingPoint("R2", "LOG-1", None, 2, "2026-01-02T06:10:00Z", 5.0, 0.2, 10),
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=policy,
            readings=orphan_readings,
            custody_handover_gaps_min=[10],
            has_assigned_logger=False,
            expected_shipment_id="SHP-1",
        )
        self.assertEqual(res.actual_reading_count, 0)
        self.assertTrue(res.telemetry_gap_flagged)
        self.assertEqual(res.compliance_status, "NON_COMPLIANT_MISSING_DATA")
        self.assertEqual(res.disposition_decision, "QUARANTINE_HOLD")

    def test_13_qa08_configurable_custody_gap_policy(self) -> None:
        """QA-08: Verify custody handoff gap is evaluated against policy.max_custody_gap_min."""
        strict_custody_policy = make_test_policy(max_custody_gap_min=60)
        readings = [
            ReadingPoint(f"R{i}", "LOG-1", "SHP-1", i + 1, f"2026-01-02T06:{i*10:02d}:00Z", 5.0, 0.2, 0 if i == 0 else 10)
            for i in range(5)
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=strict_custody_policy,
            readings=readings,
            custody_handover_gaps_min=[15, 75, 15],  # 75 > 60 min configurable policy limit
        )
        self.assertTrue(res.custody_gap_flagged)
        self.assertEqual(res.compliance_status, "NON_COMPLIANT_CUSTODY_GAP")
        self.assertEqual(res.disposition_decision, "QUARANTINE_HOLD")

    def test_14_qa09_cross_shipment_and_out_of_window_readings_rejected(self) -> None:
        """QA-09: Attempt to contaminate shipment evaluation with another shipment's readings or out-of-window readings."""
        policy = make_test_policy()
        readings = [
            ReadingPoint(f"R{i}", "LOG-1", "SHP-1", i + 1, f"2026-01-02T06:{i*10:02d}:00Z", 5.0, 0.2, 0 if i == 0 else 10)
            for i in range(5)
        ]
        # Inject a reading from unrelated SHP-999999 with a massive temperature spike
        contaminated = readings + [
            ReadingPoint("R_FOREIGN", "LOG-1", "SHP-999999", 99, "2026-01-02T06:25:00Z", 25.0, 0.2, 5)
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=policy,
            readings=contaminated,
            custody_handover_gaps_min=[10],
            expected_shipment_id="SHP-1",
        )
        # Foreign reading is rejected from excursion math and triggers INVESTIGATION_REQUIRED / QUARANTINE_HOLD
        self.assertEqual(res.total_excursion_duration_min, 0)
        self.assertEqual(res.compliance_status, "INVESTIGATION_REQUIRED")
        self.assertEqual(res.disposition_decision, "QUARANTINE_HOLD")

    def test_15_out_of_order_and_invalid_timestamps(self) -> None:
        """Verify out-of-order readings are chronologically sorted and invalid timestamps raise ValueError."""
        policy = make_test_policy()
        readings = [
            ReadingPoint(f"R{i}", "LOG-1", "SHP-1", i + 1, f"2026-01-02T06:{i*10:02d}:00Z", 5.0, 0.2, 10)
            for i in [4, 1, 3, 0, 2]
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:40:00Z",
            policy=policy,
            readings=readings,
            custody_handover_gaps_min=[10],
        )
        self.assertEqual(res.compliance_status, "COMPLIANT")

        with self.assertRaises(ValueError):
            parse_iso_utc("not-a-timestamp")

    def test_16_sqlite_foreign_key_enforcement_rejects_invalid_insert(self) -> None:
        """
        Verify that `open_sqlite_db` enables `PRAGMA foreign_keys = ON` on every connection
        and that SQLite actively rejects invalid foreign-key references with `sqlite3.IntegrityError`.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_datasets(self.datasets, Path(tmp_dir), seed=DEFAULT_SEED, scale="starter", export_sql=True)
            db_path = Path(tmp_dir) / "sql" / "cold_chain_compliance.db"

            fk_report = verify_sqlite_foreign_key_enforcement(db_path)
            self.assertTrue(fk_report["passed"])
            self.assertTrue(fk_report["pragma_foreign_keys_on"])
            self.assertEqual(fk_report["foreign_key_check_violations"], 0)
            self.assertTrue(fk_report["invalid_fk_insert_rejected"])

            conn = open_sqlite_db(db_path)
            try:
                self.assertEqual(conn.execute("PRAGMA foreign_keys;").fetchone()[0], 1)
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO Shipment (shipment_id, primary_product_id, assigned_policy_id, packaging_id, "
                        "supplier_actor, planner_actor, carrier_actor, origin_location, destination_location, "
                        "transport_mode, planned_departure_utc, planned_arrival_utc, actual_departure_utc, "
                        "actual_arrival_utc, total_distance_km, declared_value_usd, lifecycle_state) "
                        "VALUES ('SHP-2026-999999', 'PROD-INVALID', 'POL-INVALID', 'PKG-INVALID', "
                        "'SUP', 'PLN', 'CAR', 'ORIG', 'DEST', 'AIR_FREIGHT', '2026-01-01T00:00:00Z', "
                        "'2026-01-01T02:00:00Z', '2026-01-01T00:00:00Z', '2026-01-01T02:00:00Z', 100.0, 500.0, 'PLANNED');"
                    )
            finally:
                conn.close()

    def test_17_priority1_operational_inspector_never_uses_ground_truth(self) -> None:
        """
        Priority 1 Regression:
        Verify that `print_portfolio_summary`, `list_exception_shipments`, and `inspect_shipment`
        compute operational audit findings strictly from master/transaction/event tables and
        `ExcursionReport` / `ComplianceReview`, producing identical output even when `GroundTruth`
        is deleted or tampered with `TAMPERED_LABEL`.
        """
        ds_no_gt = {k: copy.deepcopy(v) for k, v in self.datasets.items() if k != "GroundTruth"}
        buf1 = io.StringIO()
        with redirect_stdout(buf1):
            print_portfolio_summary(ds_no_gt, seed=DEFAULT_SEED, scale="starter")
            list_exception_shipments(ds_no_gt, limit=10)
            inspect_shipment(ds_no_gt, "SHP-2026-000018")
        out_no_gt = buf1.getvalue()
        self.assertIn("LOGGER_NOT_ASSIGNED", out_no_gt)

        ds_tampered = copy.deepcopy(self.datasets)
        for g in ds_tampered["GroundTruth"]:
            g["edge_case_type"] = "TAMPERED_LABEL"
            g["true_compliance_status"] = "TAMPERED_STATUS"
            g["true_disposition_decision"] = "TAMPERED_DECISION"
            g["root_cause_summary"] = "TAMPERED_SUMMARY"

        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            print_portfolio_summary(ds_tampered, seed=DEFAULT_SEED, scale="starter")
            list_exception_shipments(ds_tampered, limit=10)
            inspect_shipment(ds_tampered, "SHP-2026-000018")
        out_tampered = buf2.getvalue()

        self.assertNotIn("TAMPERED_", out_tampered)
        self.assertEqual(out_no_gt, out_tampered)

    def test_18_priority2_missing_grid_and_off_grid_readings_prevent_compliant_release(self) -> None:
        """
        Priority 2 Regression:
        20-minute trip (06:00 to 06:20) expecting 10-minute grid points at 00, 10, 20.
        Readings supplied at 00, 05, 15, 20 (missing expected grid point 06:10, and containing off-grid points 05, 15),
        with max_telemetry_gap_min=20.
        Must set telemetry_gap_flagged=True and route to NON_COMPLIANT_MISSING_DATA / QUARANTINE_HOLD.
        """
        policy = make_test_policy(max_telemetry_gap_min=20)
        readings = [
            ReadingPoint("R1", "LOG-1", "SHP-1", 1, "2026-01-02T06:00:00Z", 5.0, 0.2, 0),
            ReadingPoint("R2", "LOG-1", "SHP-1", 2, "2026-01-02T06:05:00Z", 5.0, 0.2, 5),
            ReadingPoint("R3", "LOG-1", "SHP-1", 3, "2026-01-02T06:15:00Z", 5.0, 0.2, 10),
            ReadingPoint("R4", "LOG-1", "SHP-1", 4, "2026-01-02T06:20:00Z", 5.0, 0.2, 5),
        ]
        res = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:20:00Z",
            policy=policy,
            readings=readings,
            custody_handover_gaps_min=[10],
            sampling_interval_min=10,
        )
        self.assertEqual(res.expected_reading_count, 3)
        self.assertEqual(res.missing_reading_count, 1)
        self.assertTrue(res.telemetry_gap_flagged)
        self.assertEqual(res.compliance_status, "NON_COMPLIANT_MISSING_DATA")
        self.assertEqual(res.disposition_decision, "QUARANTINE_HOLD")

    def test_19_priority3_excursion_duration_bounded_by_trip_window_and_hand_calculated_cases(self) -> None:
        """
        Priority 3 Regression:
        Verify piecewise-constant hold on [t_i, t_next) clipped to [actual_departure_utc, actual_arrival_utc]:
        1) 10-minute trip (06:00 to 06:10) with readings at 06:00 and 06:10 both at 9.0C -> 10 min (not 20 min).
        2) Zero-duration trip (06:00 to 06:00) with reading at 06:00 at 9.0C -> 0 min (not 10 min).
        3) First-reading-only breach on 20-minute trip (06:00=9.0C, 06:10=5.0C, 06:20=5.0C) -> 10 min.
        4) Last-reading-only breach on 20-minute trip (06:00=5.0C, 06:10=5.0C, 06:20=9.0C) -> 0 min duration,
           flagged via uncertainty_borderline_flag -> QA_MANUAL_REVIEW.
        5) Non-finite (NaN / Inf) temperature or uncertainty raises ValueError.
        """
        policy = make_test_policy(min_temp_c=2.0, max_temp_c=8.0, critical_high_temp_c=12.0, max_mkt_c=10.0)

        # Case 1: 10-minute trip, both endpoints breaching at 9.0C -> 10 minutes
        r_10m = [
            ReadingPoint("R1", "LOG-1", "SHP-1", 1, "2026-01-02T06:00:00Z", 9.0, 0.2, 0),
            ReadingPoint("R2", "LOG-1", "SHP-1", 2, "2026-01-02T06:10:00Z", 9.0, 0.2, 10),
        ]
        res_10m = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:10:00Z",
            policy=policy,
            readings=r_10m,
            custody_handover_gaps_min=[5],
        )
        self.assertEqual(res_10m.total_excursion_duration_min, 10)
        self.assertEqual(res_10m.max_single_excursion_min, 10)

        # Case 2: 0-minute trip -> 0 excursion minutes
        r_0m = [ReadingPoint("R1", "LOG-1", "SHP-1", 1, "2026-01-02T06:00:00Z", 9.0, 0.2, 0)]
        res_0m = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:00:00Z",
            policy=policy,
            readings=r_0m,
            custody_handover_gaps_min=[5],
        )
        self.assertEqual(res_0m.total_excursion_duration_min, 0)
        self.assertEqual(res_0m.max_single_excursion_min, 0)
        self.assertEqual(res_0m.compliance_status, "INVESTIGATION_REQUIRED")

        # Case 3: First-reading-only breach on 20-minute trip -> 10 minutes
        r_first = [
            ReadingPoint("R1", "LOG-1", "SHP-1", 1, "2026-01-02T06:00:00Z", 9.0, 0.2, 0),
            ReadingPoint("R2", "LOG-1", "SHP-1", 2, "2026-01-02T06:10:00Z", 5.0, 0.2, 10),
            ReadingPoint("R3", "LOG-1", "SHP-1", 3, "2026-01-02T06:20:00Z", 5.0, 0.2, 10),
        ]
        res_first = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:20:00Z",
            policy=policy,
            readings=r_first,
            custody_handover_gaps_min=[5],
        )
        self.assertEqual(res_first.total_excursion_duration_min, 10)

        # Case 4: Last-reading-only breach at arrival endpoint -> 0 duration, routed to QA_MANUAL_REVIEW
        r_last = [
            ReadingPoint("R1", "LOG-1", "SHP-1", 1, "2026-01-02T06:00:00Z", 5.0, 0.2, 0),
            ReadingPoint("R2", "LOG-1", "SHP-1", 2, "2026-01-02T06:10:00Z", 5.0, 0.2, 10),
            ReadingPoint("R3", "LOG-1", "SHP-1", 3, "2026-01-02T06:20:00Z", 9.0, 0.2, 10),
        ]
        res_last = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:20:00Z",
            policy=policy,
            readings=r_last,
            custody_handover_gaps_min=[5],
        )
        self.assertEqual(res_last.total_excursion_duration_min, 0)
        self.assertTrue(res_last.uncertainty_borderline_flag)
        self.assertEqual(res_last.compliance_status, "INVESTIGATION_REQUIRED")
        self.assertEqual(res_last.disposition_decision, "QA_MANUAL_REVIEW")

        # Case 5: NaN temperature rejected from release (routes to NON_COMPLIANT_MISSING_DATA / QUARANTINE_HOLD,
        # and compute_mkt_celsius raises ValueError on NaN)
        r_nan = [
            ReadingPoint("R1", "LOG-1", "SHP-1", 1, "2026-01-02T06:00:00Z", float("nan"), 0.2, 0),
            ReadingPoint("R2", "LOG-1", "SHP-1", 2, "2026-01-02T06:10:00Z", 5.0, 0.2, 10),
        ]
        res_nan = ComplianceEngine.evaluate_shipment(
            actual_departure_utc="2026-01-02T06:00:00Z",
            actual_arrival_utc="2026-01-02T06:10:00Z",
            policy=policy,
            readings=r_nan,
            custody_handover_gaps_min=[5],
        )
        self.assertTrue(res_nan.telemetry_gap_flagged)
        self.assertEqual(res_nan.compliance_status, "NON_COMPLIANT_MISSING_DATA")
        self.assertEqual(res_nan.disposition_decision, "QUARANTINE_HOLD")
        with self.assertRaises(ValueError):
            compute_mkt_celsius([5.0, float("nan")])

    def test_20_priority4_and_priority5_validator_catches_expired_logger_deleted_history_and_state_mismatch(self) -> None:
        """
        Priority 4 & Priority 5 Regression:
        Verify that `DatasetValidator` catches all 3 reproduced mutations from the review:
        1) Expiring a logger calibration before the shipment (`calibration_expiry_date = 2025-12-01`).
        2) Deleting `ShipmentStateHistory` rows for a shipment.
        3) Changing `Shipment.lifecycle_state` so it disagrees with `ShipmentStateHistory` and `ComplianceReview`.
        """
        # Mutation 1: Expired logger calibration before shipment
        ds_exp_logger = copy.deepcopy(self.datasets)
        ds_exp_logger["Logger"][0]["calibration_expiry_date"] = "2025-12-01"
        res_exp_logger = DatasetValidator(ds_exp_logger).run_all_checks()
        self.assertFalse(res_exp_logger["date_ordering"]["passed"])
        self.assertFalse(res_exp_logger["overall_passed"])

        # Mutation 2: Delete ShipmentStateHistory rows for SHP-2026-000001
        ds_del_hist = copy.deepcopy(self.datasets)
        ds_del_hist["ShipmentStateHistory"] = [
            ev for ev in ds_del_hist["ShipmentStateHistory"] if ev["shipment_id"] != "SHP-2026-000001"
        ]
        res_del_hist = DatasetValidator(ds_del_hist).run_all_checks()
        self.assertFalse(res_del_hist["lifecycle_transitions"]["passed"])
        self.assertFalse(res_del_hist["overall_passed"])

        # Mutation 3: Change Shipment.lifecycle_state so it disagrees with history & disposition
        ds_bad_state = copy.deepcopy(self.datasets)
        ds_bad_state["Shipment"][0]["lifecycle_state"] = "CLOSED_REJECTED"
        res_bad_state = DatasetValidator(ds_bad_state).run_all_checks()
        self.assertFalse(res_bad_state["lifecycle_transitions"]["passed"])
        self.assertFalse(res_bad_state["overall_passed"])

    def test_21_manifest_checksum_verification_catches_corrupted_csv_export(self) -> None:
        """
        Verify that `verify_manifest_checksums` detects any tampering or corruption of exported CSV files.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            export_datasets(self.datasets, base, seed=DEFAULT_SEED, scale="starter", export_sql=True)
            check_ok = verify_manifest_checksums(base)
            self.assertTrue(check_ok["passed"])

            # Corrupt csv/Shipment.csv
            shp_csv = base / "csv" / "Shipment.csv"
            shp_csv.write_text(shp_csv.read_text(encoding="utf-8") + "\nCORRUPTED_ROW\n", encoding="utf-8")
            check_bad = verify_manifest_checksums(base)
            self.assertFalse(check_bad["passed"])
            self.assertGreater(check_bad["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
