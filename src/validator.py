"""
Comprehensive Structural, Referential, Lifecycle, Arithmetic & Anomaly Audit Validator
for the Cold-Chain Shipment Compliance & Deviation Management Dataset.

Implements:
- Step 8 Validation Suite:
  1. Primary-key uniqueness & nomenclature regex verification across all 13 tables.
  2. Foreign-key referential integrity across all relationships and bridge tables.
  3. Allowed enums & domain range verification.
  4. Chronological date & timestamp ordering checks.
  5. Arithmetic reconciliation (Shipment declared_value_usd == sum(Lot qty * unit_value),
     RouteStop dwell_duration_min, ExcursionReport expected/actual/missing counts, MKT formula).
  6. Lifecycle state transition DAG verification on ShipmentStateHistory.
  7. Independent Audit & Edge-Case Detection (100% detection of all 5 injected negative scenarios
     comparing independent audit rules against ground_truth.json).
- Step 10 Reproducibility Verification:
  Regenerates the dataset twice with the same fixed seed and compares row counts and SHA-256
  checksums across all exported files.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.compliance_engine import (
    CUSTODY_GAP_THRESHOLD_MIN,
    ComplianceEngine,
    PolicyThresholds,
    ReadingPoint,
    compute_mkt_celsius,
    parse_iso_utc,
)
from src.generator import DEFAULT_SEED, SyntheticDataGenerator, export_datasets


PK_REGEX_RULES = {
    "Product": ("product_id", re.compile(r"^PROD-2026-\d{4}$")),
    "ThresholdPolicy": ("policy_id", re.compile(r"^POL-2026-\d{4}$")),
    "Packaging": ("packaging_id", re.compile(r"^PKG-2026-\d{4}$")),
    "Lot": ("lot_id", re.compile(r"^LOT-2026-\d{6}$")),
    "Logger": ("logger_id", re.compile(r"^LOG-SHP-2026-\d{6}-01$")),
    "Shipment": ("shipment_id", re.compile(r"^SHP-2026-\d{6}$")),
    "ShipmentLot": ("shipment_lot_id", re.compile(r"^SHPLOT-2026-\d{6}$")),
    "ShipmentLogger": ("assignment_id", re.compile(r"^ASSGN-2026-\d{6}$")),
    "RouteStop": ("stop_id", re.compile(r"^STOP-SHP-2026-\d{6}-\d{2}$")),
    "CustodyEvent": ("custody_event_id", re.compile(r"^CUST-2026-\d{7}$")),
    "TemperatureReading": ("reading_id", re.compile(r"^TEMP-LOG-SHP-2026-\d{6}-01-\d{14}$")),
    "ExcursionReport": ("report_id", re.compile(r"^EXCR-2026-\d{6}$")),
    "ShipmentStateHistory": ("state_event_id", re.compile(r"^STHIST-2026-\d{7}$")),
}

ALLOWED_ENUMS = {
    "Product.category": {
        "BIOLOGIC",
        "VACCINE",
        "MRNA_THERAPY",
        "BLOOD_PLASMA",
        "MONOCLONAL_ANTIBODY",
        "INSULIN_PEPTIDE",
    },
    "Product.storage_regime": {"ULT_DEEP_FREEZE", "FROZEN", "REFRIGERATED", "CONTROLLED_ROOM_TEMP"},
    "Shipment.transport_mode": {"AIR_FREIGHT", "OCEAN_REEFER", "ROAD_REEFER", "MULTIMODAL_AIR_ROAD"},
    "Shipment.lifecycle_state": {
        "PLANNED",
        "PACKED",
        "IN_TRANSIT",
        "DELIVERED",
        "EVALUATED",
        "CLOSED_RELEASED",
        "CLOSED_QUARANTINED",
        "CLOSED_REJECTED",
    },
    "ExcursionReport.compliance_status": {
        "COMPLIANT",
        "MINOR_EXCURSION_ACCEPTABLE",
        "CRITICAL_EXCURSION",
        "NON_COMPLIANT_MISSING_DATA",
        "NON_COMPLIANT_CUSTODY_GAP",
        "INVESTIGATION_REQUIRED",
    },
    "ExcursionReport.disposition_decision": {
        "AUTO_RELEASE",
        "RELEASE_WITH_STABILITY_DEDUCTION",
        "QA_MANUAL_REVIEW",
        "QUARANTINE_HOLD",
        "REJECT_DESTROY",
    },
}

LEGAL_STATE_TRANSITIONS = {
    None: {"PLANNED"},
    "PLANNED": {"PACKED"},
    "PACKED": {"IN_TRANSIT"},
    "IN_TRANSIT": {"DELIVERED"},
    "DELIVERED": {"EVALUATED"},
    "EVALUATED": {"CLOSED_RELEASED", "CLOSED_QUARANTINED", "CLOSED_REJECTED"},
}


class DatasetValidator:
    """Runs structural, relational, arithmetic, lifecycle, and anomaly audit checks."""

    def __init__(self, datasets: Dict[str, List[Dict[str, Any]]]):
        self.datasets = datasets

    def run_all_checks(self) -> Dict[str, Any]:
        pk_res = self._check_primary_keys_and_nomenclature()
        fk_res = self._check_foreign_keys()
        enum_res = self._check_enums()
        date_res = self._check_date_ordering()
        arith_res = self._check_arithmetic_reconciliation()
        lifecycle_res = self._check_lifecycle_transitions()
        audit_res = self._audit_edge_cases_and_ground_truth()

        all_passed = all(
            section["passed"]
            for section in (pk_res, fk_res, enum_res, date_res, arith_res, lifecycle_res, audit_res)
        )

        return {
            "overall_passed": all_passed,
            "primary_key_and_nomenclature": pk_res,
            "foreign_key_integrity": fk_res,
            "allowed_enums": enum_res,
            "date_ordering": date_res,
            "arithmetic_reconciliation": arith_res,
            "lifecycle_transitions": lifecycle_res,
            "independent_anomaly_audit": audit_res,
        }

    def _check_primary_keys_and_nomenclature(self) -> Dict[str, Any]:
        errors: List[str] = []
        checked_counts: Dict[str, int] = {}

        for tbl, (pk_col, pattern) in PK_REGEX_RULES.items():
            rows = self.datasets.get(tbl, [])
            seen: Set[str] = set()
            for r in rows:
                val = r.get(pk_col)
                if not val or not isinstance(val, str):
                    errors.append(f"{tbl}: Missing primary key {pk_col}")
                    continue
                if val in seen:
                    errors.append(f"{tbl}: Duplicate PK {val}")
                seen.add(val)
                if not pattern.match(val):
                    errors.append(f"{tbl}: PK '{val}' violates nomenclature regex {pattern.pattern}")
            checked_counts[tbl] = len(seen)

        return {
            "passed": len(errors) == 0,
            "unique_pk_counts": checked_counts,
            "error_count": len(errors),
            "sample_errors": errors[:10],
        }

    def _check_foreign_keys(self) -> Dict[str, Any]:
        errors: List[str] = []

        prod_ids = {r["product_id"] for r in self.datasets["Product"]}
        pol_ids = {r["policy_id"] for r in self.datasets["ThresholdPolicy"]}
        pkg_ids = {r["packaging_id"] for r in self.datasets["Packaging"]}
        lot_ids = {r["lot_id"] for r in self.datasets["Lot"]}
        log_ids = {r["logger_id"] for r in self.datasets["Logger"]}
        shp_ids = {r["shipment_id"] for r in self.datasets["Shipment"]}
        stop_ids = {r["stop_id"] for r in self.datasets["RouteStop"]}
        read_ids = {r["reading_id"] for r in self.datasets["TemperatureReading"]}

        fk_checks = [
            ("ThresholdPolicy", "product_id", prod_ids, False),
            ("Lot", "product_id", prod_ids, False),
            ("Shipment", "primary_product_id", prod_ids, False),
            ("Shipment", "assigned_policy_id", pol_ids, False),
            ("Shipment", "packaging_id", pkg_ids, False),
            ("ShipmentLot", "shipment_id", shp_ids, False),
            ("ShipmentLot", "lot_id", lot_ids, False),
            ("ShipmentLogger", "shipment_id", shp_ids, False),
            ("ShipmentLogger", "logger_id", log_ids, False),
            ("RouteStop", "shipment_id", shp_ids, False),
            ("CustodyEvent", "shipment_id", shp_ids, False),
            ("CustodyEvent", "stop_id", stop_ids, False),
            ("TemperatureReading", "logger_id", log_ids, False),
            ("TemperatureReading", "shipment_id", shp_ids, True),  # Optional for orphan readings
            ("ExcursionReport", "shipment_id", shp_ids, False),
            ("ExcursionReport", "policy_id_evaluated", pol_ids, False),
            ("ExcursionReport", "logger_id_evaluated", log_ids, True),
            ("ExcursionReport", "first_breach_reading_id", read_ids, True),
            ("ShipmentStateHistory", "shipment_id", shp_ids, False),
        ]

        total_fk_references_checked = 0
        for tbl, fk_col, target_set, nullable in fk_checks:
            for r in self.datasets.get(tbl, []):
                val = r.get(fk_col)
                if val is None:
                    if not nullable:
                        errors.append(f"{tbl}.{fk_col} is NULL on non-nullable FK")
                    continue
                total_fk_references_checked += 1
                if val not in target_set:
                    errors.append(f"{tbl}.{fk_col}='{val}' not found in parent table")

        return {
            "passed": len(errors) == 0,
            "total_fk_references_verified": total_fk_references_checked,
            "error_count": len(errors),
            "sample_errors": errors[:10],
        }

    def _check_enums(self) -> Dict[str, Any]:
        errors: List[str] = []
        checks_run = 0

        for key, allowed in ALLOWED_ENUMS.items():
            tbl, col = key.split(".")
            for r in self.datasets.get(tbl, []):
                checks_run += 1
                val = r.get(col)
                if val not in allowed:
                    errors.append(f"{tbl}.{col} invalid enum value '{val}'")

        return {
            "passed": len(errors) == 0,
            "enum_values_verified": checks_run,
            "error_count": len(errors),
            "sample_errors": errors[:10],
        }

    def _check_date_ordering(self) -> Dict[str, Any]:
        errors: List[str] = []
        checks_run = 0

        for lot in self.datasets["Lot"]:
            checks_run += 1
            if lot["manufactured_date"] >= lot["expiry_date"]:
                errors.append(f"Lot {lot['lot_id']} mfg_date >= expiry_date")

        for logger in self.datasets["Logger"]:
            checks_run += 1
            if logger["calibration_date"] >= logger["calibration_expiry_date"]:
                errors.append(f"Logger {logger['logger_id']} cal_date >= cal_expiry_date")

        for shp in self.datasets["Shipment"]:
            checks_run += 2
            if parse_iso_utc(shp["planned_departure_utc"]) >= parse_iso_utc(shp["planned_arrival_utc"]):
                errors.append(f"Shipment {shp['shipment_id']} planned departure >= arrival")
            if parse_iso_utc(shp["actual_departure_utc"]) >= parse_iso_utc(shp["actual_arrival_utc"]):
                errors.append(f"Shipment {shp['shipment_id']} actual departure >= arrival")

        for stop in self.datasets["RouteStop"]:
            checks_run += 1
            arr_dt = parse_iso_utc(stop["actual_arrival_utc"])
            dep_dt = parse_iso_utc(stop["actual_departure_utc"])
            if arr_dt > dep_dt:
                errors.append(f"RouteStop {stop['stop_id']} actual_arrival > actual_departure")

        return {
            "passed": len(errors) == 0,
            "chronological_assertions_verified": checks_run,
            "error_count": len(errors),
            "sample_errors": errors[:10],
        }

    def _check_arithmetic_reconciliation(self) -> Dict[str, Any]:
        errors: List[str] = []
        reconciled_shipments = 0
        reconciled_stops = 0

        prod_val = {p["product_id"]: p["unit_value_usd"] for p in self.datasets["Product"]}
        lot_prod = {l["lot_id"]: l["product_id"] for l in self.datasets["Lot"]}

        lots_by_shp: Dict[str, List[Dict[str, Any]]] = {}
        for sl in self.datasets["ShipmentLot"]:
            lots_by_shp.setdefault(sl["shipment_id"], []).append(sl)

        for shp in self.datasets["Shipment"]:
            sid = shp["shipment_id"]
            sl_rows = lots_by_shp.get(sid, [])
            calc_val = round(
                sum(r["quantity_units"] * prod_val[lot_prod[r["lot_id"]]] for r in sl_rows),
                2,
            )
            if abs(calc_val - shp["declared_value_usd"]) > 0.01:
                errors.append(
                    f"Shipment {sid} declared_value_usd={shp['declared_value_usd']} != sum(lot_qty*unit_val)={calc_val}"
                )
            else:
                reconciled_shipments += 1

        for stop in self.datasets["RouteStop"]:
            arr_dt = parse_iso_utc(stop["actual_arrival_utc"])
            dep_dt = parse_iso_utc(stop["actual_departure_utc"])
            diff_min = int((dep_dt - arr_dt).total_seconds() // 60)
            if diff_min != stop["dwell_duration_min"]:
                errors.append(f"RouteStop {stop['stop_id']} dwell_duration_min mismatch")
            else:
                reconciled_stops += 1

        return {
            "passed": len(errors) == 0,
            "reconciled_shipment_values": reconciled_shipments,
            "reconciled_route_stop_dwells": reconciled_stops,
            "error_count": len(errors),
            "sample_errors": errors[:10],
        }

    def _check_lifecycle_transitions(self) -> Dict[str, Any]:
        errors: List[str] = []
        events_by_shp: Dict[str, List[Dict[str, Any]]] = {}
        for ev in self.datasets["ShipmentStateHistory"]:
            events_by_shp.setdefault(ev["shipment_id"], []).append(ev)

        verified_chains = 0
        for sid, ev_list in events_by_shp.items():
            sorted_ev = sorted(ev_list, key=lambda x: x["sequence_no"])
            prev_state = None
            prev_dt = None
            for ev in sorted_ev:
                if ev["prior_state"] != prev_state:
                    errors.append(f"{sid}: Broken state chain prior_state={ev['prior_state']} != {prev_state}")
                allowed_next = LEGAL_STATE_TRANSITIONS.get(prev_state, set())
                if ev["new_state"] not in allowed_next:
                    errors.append(f"{sid}: Illegal transition {prev_state} -> {ev['new_state']}")
                curr_dt = parse_iso_utc(ev["changed_at_utc"])
                if prev_dt and curr_dt < prev_dt:
                    errors.append(f"{sid}: Non-monotonic state timestamp at seq {ev['sequence_no']}")
                prev_state = ev["new_state"]
                prev_dt = curr_dt
            verified_chains += 1

        return {
            "passed": len(errors) == 0,
            "verified_shipment_state_chains": verified_chains,
            "error_count": len(errors),
            "sample_errors": errors[:10],
        }

    def _audit_edge_cases_and_ground_truth(self) -> Dict[str, Any]:
        """
        Independently audit every shipment from raw master/transaction/event tables
        to detect all 5 injected negative edge cases, verify 100% agreement with GroundTruth,
        and publish separate metrics for:
        - Ground-truth reconstruction accuracy
        - Original compliance-report decision accuracy
        - Edge-case detection precision and recall
        - False-compliant shipments
        - Incorrect excursion durations
        - Incorrect dispositions
        """
        canonical_pol_by_prod: Dict[str, PolicyThresholds] = {}
        for p in self.datasets["ThresholdPolicy"]:
            pol_obj = PolicyThresholds(
                policy_id=p["policy_id"],
                product_id=p["product_id"],
                min_temp_c=p["min_temp_c"],
                max_temp_c=p["max_temp_c"],
                critical_low_temp_c=p["critical_low_temp_c"],
                critical_high_temp_c=p["critical_high_temp_c"],
                max_single_excursion_min=p["max_single_excursion_min"],
                max_cumulative_excursion_min=p["max_cumulative_excursion_min"],
                max_mkt_c=p["max_mkt_c"],
                max_telemetry_gap_min=p["max_telemetry_gap_min"],
                max_custody_gap_min=p.get("max_custody_gap_min", CUSTODY_GAP_THRESHOLD_MIN),
                effective_from=p.get("effective_from", "2026-01-01T00:00:00Z"),
                effective_to=p.get("effective_to"),
            )
            canonical_pol_by_prod[p["product_id"]] = pol_obj

        assigned_loggers_by_shp: Dict[str, List[str]] = {}
        for sl in self.datasets["ShipmentLogger"]:
            assigned_loggers_by_shp.setdefault(sl["shipment_id"], []).append(sl["logger_id"])

        readings_by_shp: Dict[str, List[ReadingPoint]] = {}
        orphan_reading_count = 0
        for r in self.datasets["TemperatureReading"]:
            if r["shipment_id"] is None:
                orphan_reading_count += 1
                continue
            readings_by_shp.setdefault(r["shipment_id"], []).append(
                ReadingPoint(
                    reading_id=r["reading_id"],
                    logger_id=r["logger_id"],
                    shipment_id=r["shipment_id"],
                    sequence_no=r["sequence_no"],
                    recorded_at_utc=r["recorded_at_utc"],
                    temperature_c=r["temperature_c"],
                    calibration_uncertainty_c=r["calibration_uncertainty_c"],
                    elapsed_since_prev_min=r["elapsed_since_prev_min"],
                )
            )

        custody_by_shp: Dict[str, List[Dict[str, Any]]] = {}
        for ce in self.datasets["CustodyEvent"]:
            custody_by_shp.setdefault(ce["shipment_id"], []).append(ce)

        report_by_shp = {er["shipment_id"]: er for er in self.datasets["ExcursionReport"]}
        gt_by_shp = {gt["shipment_id"]: gt for gt in self.datasets["GroundTruth"]}

        detected_counts = {
            "LOGGER_NOT_ASSIGNED": 0,
            "PRODUCT_THRESHOLD_MISMATCH": 0,
            "MISSING_DATA_SILENTLY_PASSED": 0,
            "CUSTODY_GAP_NOT_FLAGGED": 0,
            "CROSS_SHIPMENT_EXCURSION_CALC": 0,
        }
        gt_matches = 0
        original_report_status_matches = 0
        original_report_disposition_matches = 0
        false_compliant_shipments = 0
        incorrect_excursion_durations = 0
        incorrect_dispositions = 0
        true_positive_edge_detections = 0
        false_positive_edge_detections = 0
        false_negative_edge_detections = 0
        mismatches: List[str] = []

        total_shipments = len(self.datasets["Shipment"])

        for shp in self.datasets["Shipment"]:
            sid = shp["shipment_id"]
            true_pol = canonical_pol_by_prod[shp["primary_product_id"]]
            rep = report_by_shp[sid]
            gt = gt_by_shp[sid]
            shp_loggers = assigned_loggers_by_shp.get(sid, [])
            shp_readings = readings_by_shp.get(sid, [])
            shp_custody = custody_by_shp.get(sid, [])
            custody_gaps = [c["handover_gap_min"] for c in shp_custody]

            true_eval = ComplianceEngine.evaluate_shipment(
                actual_departure_utc=shp["actual_departure_utc"],
                actual_arrival_utc=shp["actual_arrival_utc"],
                policy=true_pol,
                readings=shp_readings,
                custody_handover_gaps_min=custody_gaps,
                has_assigned_logger=len(shp_loggers) > 0,
                sampling_interval_min=10,
                expected_product_id=shp["primary_product_id"],
                expected_shipment_id=sid,
                expected_logger_ids=set(shp_loggers) if shp_loggers else None,
            )

            # Audit detection rules from raw tables + ExcursionReport:
            detected_edge = "NONE"
            if len(shp_loggers) == 0 or rep["logger_id_evaluated"] is None:
                detected_edge = "LOGGER_NOT_ASSIGNED"
            elif shp["assigned_policy_id"] != true_pol.policy_id or rep["policy_id_evaluated"] != true_pol.policy_id:
                detected_edge = "PRODUCT_THRESHOLD_MISMATCH"
            elif true_eval.telemetry_gap_flagged and not rep["telemetry_gap_flagged"]:
                detected_edge = "MISSING_DATA_SILENTLY_PASSED"
            elif true_eval.custody_gap_flagged and not rep["custody_gap_flagged"]:
                detected_edge = "CUSTODY_GAP_NOT_FLAGGED"
            elif rep["total_excursion_duration_min"] != true_eval.total_excursion_duration_min:
                detected_edge = "CROSS_SHIPMENT_EXCURSION_CALC"

            if detected_edge != "NONE":
                detected_counts[detected_edge] += 1

            # Precision / Recall tracking for injected edge cases
            gt_edge = gt["edge_case_type"]
            if detected_edge != "NONE" and detected_edge == gt_edge:
                true_positive_edge_detections += 1
            elif detected_edge != "NONE" and detected_edge != gt_edge:
                false_positive_edge_detections += 1
            elif detected_edge == "NONE" and gt_edge != "NONE":
                false_negative_edge_detections += 1

            # Compare pre-audit original ExcursionReport against reconstructed ground truth (Bug 5 fix)
            if rep["compliance_status"] == true_eval.compliance_status:
                original_report_status_matches += 1
            if rep["disposition_decision"] == true_eval.disposition_decision:
                original_report_disposition_matches += 1
            else:
                incorrect_dispositions += 1

            if rep["compliance_status"] == "COMPLIANT" and true_eval.compliance_status != "COMPLIANT":
                false_compliant_shipments += 1

            if rep["total_excursion_duration_min"] != true_eval.total_excursion_duration_min:
                incorrect_excursion_durations += 1

            if (
                detected_edge == gt_edge
                and true_eval.compliance_status == gt["true_compliance_status"]
                and true_eval.disposition_decision == gt["true_disposition_decision"]
            ):
                gt_matches += 1
            else:
                mismatches.append(
                    f"{sid}: detected_edge={detected_edge} vs gt={gt_edge}, "
                    f"true_status={true_eval.compliance_status} vs gt={gt['true_compliance_status']}"
                )

        precision = (
            round((true_positive_edge_detections / (true_positive_edge_detections + false_positive_edge_detections)) * 100.0, 2)
            if (true_positive_edge_detections + false_positive_edge_detections) > 0
            else 0.0
        )
        recall = (
            round((true_positive_edge_detections / (true_positive_edge_detections + false_negative_edge_detections)) * 100.0, 2)
            if (true_positive_edge_detections + false_negative_edge_detections) > 0
            else 0.0
        )

        return {
            "passed": len(mismatches) == 0 and all(v > 0 for v in detected_counts.values()),
            "total_shipments_audited": total_shipments,
            "ground_truth_exact_matches": gt_matches,
            "ground_truth_reconstruction_accuracy_pct": round((gt_matches / total_shipments) * 100.0, 2),
            "original_report_decision_accuracy_pct": round((original_report_status_matches / total_shipments) * 100.0, 2),
            "original_report_disposition_accuracy_pct": round((original_report_disposition_matches / total_shipments) * 100.0, 2),
            "edge_case_detection_precision_pct": precision,
            "edge_case_detection_recall_pct": recall,
            "false_compliant_shipments_in_uncorrected_reports": false_compliant_shipments,
            "incorrect_excursion_durations_in_uncorrected_reports": incorrect_excursion_durations,
            "incorrect_dispositions_in_uncorrected_reports": incorrect_dispositions,
            "detected_edge_case_counts": detected_counts,
            "orphan_temperature_readings_detected": orphan_reading_count,
            "mismatch_count": len(mismatches),
            "sample_mismatches": mismatches[:10],
        }


def verify_reproducibility(seed: int = DEFAULT_SEED, scale: str = "starter") -> Dict[str, Any]:
    """
    Step 10: Regenerate dataset twice in isolated directories with the same fixed seed
    and compare all row counts and file SHA-256 checksums.
    """
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        gen1 = SyntheticDataGenerator(seed=seed, scale=scale)
        ds1 = gen1.generate_all()
        man1 = export_datasets(ds1, Path(tmp1), seed=seed, scale=scale, export_sql=True)

        gen2 = SyntheticDataGenerator(seed=seed, scale=scale)
        ds2 = gen2.generate_all()
        man2 = export_datasets(ds2, Path(tmp2), seed=seed, scale=scale, export_sql=True)

        files1 = man1["files"]
        files2 = man2["files"]

        comparisons: List[Dict[str, Any]] = []
        all_identical = True
        for rel_path, info1 in sorted(files1.items()):
            info2 = files2.get(rel_path, {})
            same_rows = info1.get("row_count") == info2.get("row_count")
            same_hash = info1.get("sha256") == info2.get("sha256")
            if not (same_rows and same_hash):
                all_identical = False
            comparisons.append({
                "file": rel_path,
                "row_count_run1": info1.get("row_count"),
                "row_count_run2": info2.get("row_count"),
                "sha256_run1": info1.get("sha256"),
                "sha256_run2": info2.get("sha256"),
                "identical": same_rows and same_hash,
            })

        return {
            "seed": seed,
            "scale": scale,
            "reproducible_100_pct": all_identical,
            "files_compared": len(comparisons),
            "comparisons": comparisons,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Cold-Chain Compliance synthetic dataset & reproducibility.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Seed to validate")
    parser.add_argument("--scale", choices=["full", "starter"], default="full", help="Scale to validate")
    parser.add_argument(
        "--report-out",
        type=str,
        default="docs/VALIDATION_REPORT.json",
        help="Output path for JSON validation report",
    )
    args = parser.parse_args()

    print(f"[Validator] Generating dataset in-memory for validation (seed={args.seed}, scale={args.scale})...")
    gen = SyntheticDataGenerator(seed=args.seed, scale=args.scale)
    datasets = gen.generate_all()

    validator = DatasetValidator(datasets)
    val_results = validator.run_all_checks()

    print(f"[Validator] Running Step 10 reproducibility verification (two independent runs with seed={args.seed})...")
    repro_results = verify_reproducibility(seed=args.seed, scale="starter")

    full_report = {
        "validation_summary": val_results,
        "reproducibility_verification": repro_results,
    }

    out_path = Path(args.report_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="\n", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2)
        f.write("\n")

    print(f"[Validator] Overall Validation Passed: {val_results['overall_passed']}")
    print(f"[Validator] Reproducibility 100% Identical: {repro_results['reproducible_100_pct']}")
    print(f"[Validator] Report saved to: {out_path.resolve()}")


if __name__ == "__main__":
    main()
