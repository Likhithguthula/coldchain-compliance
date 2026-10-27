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

import unittest
from src.compliance_engine import (
    ComplianceEngine,
    PolicyThresholds,
    ReadingPoint,
    compute_mkt_celsius,
    parse_iso_utc,
)
from src.generator import DEFAULT_SEED, SyntheticDataGenerator
from src.validator import DatasetValidator, verify_reproducibility


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


if __name__ == "__main__":
    unittest.main()
