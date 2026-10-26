"""
Automated Test Suite for the YottaFlex Cold-Chain Shipment Compliance & Deviation Management Solution.

Run via:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from src.compliance_engine import ComplianceEngine, PolicyThresholds, ReadingPoint, compute_mkt_celsius
from src.generator import DEFAULT_SEED, SyntheticDataGenerator
from src.validator import DatasetValidator, verify_reproducibility


class TestColdChainCompliancePlatform(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.gen = SyntheticDataGenerator(seed=DEFAULT_SEED, scale="starter")
        cls.datasets = cls.gen.generate_all()
        cls.validator = DatasetValidator(cls.datasets)
        cls.val_report = cls.validator.run_all_checks()

    def test_01_mkt_haynes_formula(self) -> None:
        """Verify Haynes Mean Kinetic Temperature (MKT) calculation accuracy."""
        # Constant temperature readings must yield an MKT equal to that constant temperature
        constant_temps = [5.0, 5.0, 5.0, 5.0, 5.0]
        self.assertEqual(compute_mkt_celsius(constant_temps), 5.0)

        # Variable temperatures must yield an MKT strictly higher than the arithmetic mean due to Arrhenius weighting
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

    def test_05_five_negative_edge_cases_and_ground_truth(self) -> None:
        """Verify Step 5 & Step 7: all 5 required negative edge cases are present and independently detected."""
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

    def test_06_step10_fixed_seed_reproducibility(self) -> None:
        """Verify Step 10: Regenerating twice with the same seed yields identical row counts and SHA-256 hashes."""
        repro = verify_reproducibility(seed=DEFAULT_SEED, scale="starter")
        self.assertTrue(repro["reproducible_100_pct"])


if __name__ == "__main__":
    unittest.main()
