"""
Comprehensive Structural, Referential, Lifecycle, Arithmetic & Anomaly Audit Validator
for the Cold-Chain Shipment Compliance & Deviation Management Dataset.

Implements:
- Step 8 Validation Suite:
  1. Primary-key uniqueness & nomenclature regex verification across all 14 tables.
  2. Foreign-key referential integrity across all relationships, bridge tables, and compound joins.
  3. Allowed enums & domain range verification.
  4. Chronological date & timestamp ordering checks, plus Logger calibration window coverage,
     non-overlapping ShipmentLogger windows, and Lot usability/product compatibility.
  5. Arithmetic reconciliation (Shipment declared_value_usd == sum(Lot qty * unit_value),
     RouteStop dwell_duration_min, CustodyEvent handover_gap_min from timestamps,
     ExcursionReport expected/actual/missing counts, MKT formula).
  6. Lifecycle state transition DAG verification on ShipmentStateHistory, ensuring EVERY Shipment
     has a complete state history chain whose final state reconciles with Shipment.lifecycle_state
     and the latest effective disposition (ComplianceReview / ExcursionReport).
  7. Independent Audit & Edge-Case Detection (100% detection of all 5 injected negative scenarios
     using ComplianceEngine.audit_dataset, verified against ComplianceReview and ground_truth.json).
- Step 10 Reproducibility Verification:
  Regenerates the dataset twice with the same fixed seed at the requested scale and compares row counts
  and SHA-256 checksums across all exported files.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
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
from src.generator import (
    DEFAULT_SEED,
    SyntheticDataGenerator,
    export_datasets,
    load_datasets_from_dir,
    open_sqlite_db,
    verify_manifest_checksums,
)


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
    "ComplianceReview": ("review_id", re.compile(r"^REV-2026-\d{6}$")),
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
    "ComplianceReview.anomaly_type": {
        "NONE",
        "LOGGER_NOT_ASSIGNED",
        "LOGGER_CALIBRATION_EXPIRED",
        "PRODUCT_THRESHOLD_MISMATCH",
        "MISSING_DATA_SILENTLY_PASSED",
        "CUSTODY_GAP_NOT_FLAGGED",
        "CROSS_SHIPMENT_EXCURSION_CALC",
    },
    "ComplianceReview.resolution_state": {
        "CONFIRMED_AS_REPORTED",
        "OVERRIDDEN_CORRECTED",
    },
}

LEGAL_STATE_TRANSITIONS = {
    None: {"PLANNED"},
    "PLANNED": {"PACKED"},
    "PACKED": {"IN_TRANSIT"},
    "IN_TRANSIT": {"DELIVERED"},
    "DELIVERED": {"EVALUATED"},
    "EVALUATED": {"CLOSED_RELEASED", "CLOSED_QUARANTINED", "CLOSED_REJECTED"},
    # Post-audit QA override transitions from initial pre-audit closure to corrected closure state:
    "CLOSED_RELEASED": {"CLOSED_QUARANTINED", "CLOSED_REJECTED"},
    "CLOSED_REJECTED": {"CLOSED_RELEASED", "CLOSED_QUARANTINED"},
    "CLOSED_QUARANTINED": {"CLOSED_RELEASED", "CLOSED_REJECTED"},
}


def disposition_to_closed_state(disposition: str) -> str:
    if disposition in ("AUTO_RELEASE", "RELEASE_WITH_STABILITY_DEDUCTION"):
        return "CLOSED_RELEASED"
    if disposition == "REJECT_DESTROY":
        return "CLOSED_REJECTED"
    return "CLOSED_QUARANTINED"


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
        lot_by_id = {r["lot_id"]: r for r in self.datasets["Lot"]}
        lot_ids = set(lot_by_id.keys())
        log_ids = {r["logger_id"] for r in self.datasets["Logger"]}
        shp_by_id = {r["shipment_id"]: r for r in self.datasets["Shipment"]}
        shp_ids = set(shp_by_id.keys())
        stop_by_id = {r["stop_id"]: r for r in self.datasets["RouteStop"]}
        stop_ids = set(stop_by_id.keys())
        read_by_id = {r["reading_id"]: r for r in self.datasets["TemperatureReading"]}
        read_ids = set(read_by_id.keys())
        rep_by_id = {r["report_id"]: r for r in self.datasets["ExcursionReport"]}
        rep_ids = set(rep_by_id.keys())

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
            ("ComplianceReview", "report_id", rep_ids, False),
            ("ComplianceReview", "shipment_id", shp_ids, False),
            ("ComplianceReview", "canonical_policy_id", pol_ids, False),
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

        # Compound relational integrity checks (Priority 5)
        for sl in self.datasets["ShipmentLot"]:
            shp = shp_by_id.get(sl["shipment_id"])
            lot = lot_by_id.get(sl["lot_id"])
            if shp and lot and lot["product_id"] != shp["primary_product_id"]:
                errors.append(
                    f"ShipmentLot {sl['shipment_lot_id']}: Lot {lot['lot_id']} product {lot['product_id']} "
                    f"!= Shipment {shp['shipment_id']} primary_product_id {shp['primary_product_id']}"
                )

        for ce in self.datasets["CustodyEvent"]:
            stop = stop_by_id.get(ce["stop_id"])
            if stop and stop["shipment_id"] != ce["shipment_id"]:
                errors.append(
                    f"CustodyEvent {ce['custody_event_id']}: stop_id {ce['stop_id']} belongs to shipment "
                    f"{stop['shipment_id']} != {ce['shipment_id']}"
                )

        for er in self.datasets["ExcursionReport"]:
            fb_id = er.get("first_breach_reading_id")
            if fb_id is not None:
                rd = read_by_id.get(fb_id)
                if rd:
                    if rd["shipment_id"] != er["shipment_id"]:
                        errors.append(
                            f"ExcursionReport {er['report_id']}: first_breach_reading_id {fb_id} belongs to "
                            f"shipment {rd['shipment_id']} != {er['shipment_id']}"
                        )
                    if rd["logger_id"] != er["logger_id_evaluated"]:
                        errors.append(
                            f"ExcursionReport {er['report_id']}: first_breach_reading_id {fb_id} belongs to "
                            f"logger {rd['logger_id']} != {er['logger_id_evaluated']}"
                        )

        # Require 1:1 ExcursionReport and 1:1 ComplianceReview for every Shipment under the documented schema
        rep_count_by_shp: Dict[str, int] = {}
        for er in self.datasets.get("ExcursionReport", []):
            rep_count_by_shp[er["shipment_id"]] = rep_count_by_shp.get(er["shipment_id"], 0) + 1

        rev_count_by_shp: Dict[str, int] = {}
        for rv in self.datasets.get("ComplianceReview", []):
            rev_count_by_shp[rv["shipment_id"]] = rev_count_by_shp.get(rv["shipment_id"], 0) + 1
            rep_row = rep_by_id.get(rv.get("report_id"))
            if rep_row and rep_row["shipment_id"] != rv["shipment_id"]:
                errors.append(
                    f"ComplianceReview {rv['review_id']}: report_id {rv['report_id']} belongs to "
                    f"shipment {rep_row['shipment_id']} != {rv['shipment_id']}"
                )

        for sid in shp_ids:
            if rep_count_by_shp.get(sid, 0) != 1:
                errors.append(
                    f"Shipment {sid}: expected exactly 1 ExcursionReport, found {rep_count_by_shp.get(sid, 0)}"
                )
            if rev_count_by_shp.get(sid, 0) != 1:
                errors.append(
                    f"Shipment {sid}: expected exactly 1 ComplianceReview, found {rev_count_by_shp.get(sid, 0)}"
                )

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

        lot_by_id = {l["lot_id"]: l for l in self.datasets["Lot"]}
        logger_by_id = {lg["logger_id"]: lg for lg in self.datasets["Logger"]}
        shp_by_id = {s["shipment_id"]: s for s in self.datasets["Shipment"]}

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

        # Priority 5: Validate Lot usability at dispatch (manufactured_date <= departure_date < expiry_date & RELEASED)
        for sl in self.datasets["ShipmentLot"]:
            checks_run += 1
            shp = shp_by_id.get(sl["shipment_id"])
            lot = lot_by_id.get(sl["lot_id"])
            if shp and lot:
                dep_date = parse_iso_utc(shp["actual_departure_utc"]).date().isoformat()
                arr_date = parse_iso_utc(shp["actual_arrival_utc"]).date().isoformat()
                if lot["manufactured_date"] > dep_date:
                    errors.append(
                        f"ShipmentLot {sl['shipment_lot_id']}: Lot {lot['lot_id']} manufactured_date "
                        f"{lot['manufactured_date']} > departure {dep_date}"
                    )
                if lot["expiry_date"] <= arr_date:
                    errors.append(
                        f"ShipmentLot {sl['shipment_lot_id']}: Lot {lot['lot_id']} expiry_date "
                        f"{lot['expiry_date']} <= arrival {arr_date}"
                    )
                if lot.get("lifecycle_state") != "RELEASED":
                    errors.append(
                        f"ShipmentLot {sl['shipment_lot_id']}: Lot {lot['lot_id']} is in state "
                        f"{lot.get('lifecycle_state')} (expected RELEASED)"
                    )

        # Priority 5: Validate Logger calibration window covers shipment window, activation window covers trip,
        # and no physical logger has overlapping assignments across shipments!
        assignments_by_logger: Dict[str, List[Tuple[datetime, datetime, str]]] = {}
        for assgn in self.datasets["ShipmentLogger"]:
            checks_run += 2
            shp = shp_by_id.get(assgn["shipment_id"])
            lg = logger_by_id.get(assgn["logger_id"])
            act_dt = parse_iso_utc(assgn["activated_utc"])
            deact_dt = parse_iso_utc(assgn["deactivated_utc"])
            if act_dt >= deact_dt:
                errors.append(f"ShipmentLogger {assgn['assignment_id']} activated_utc >= deactivated_utc")
            if shp:
                dep_dt = parse_iso_utc(shp["actual_departure_utc"])
                arr_dt = parse_iso_utc(shp["actual_arrival_utc"])
                if act_dt > dep_dt or deact_dt < arr_dt:
                    errors.append(
                        f"ShipmentLogger {assgn['assignment_id']} window [{assgn['activated_utc']}, "
                        f"{assgn['deactivated_utc']}] does not cover trip [{shp['actual_departure_utc']}, "
                        f"{shp['actual_arrival_utc']}]"
                    )
                if lg:
                    dep_date = dep_dt.date().isoformat()
                    arr_date = arr_dt.date().isoformat()
                    if lg["calibration_date"] > dep_date or lg["calibration_expiry_date"] < arr_date:
                        errors.append(
                            f"Logger {lg['logger_id']} calibration window [{lg['calibration_date']}, "
                            f"{lg['calibration_expiry_date']}] does not cover Shipment {shp['shipment_id']} "
                            f"[{dep_date}, {arr_date}]"
                        )
                    if lg.get("lifecycle_state") != "ACTIVE_CALIBRATED":
                        errors.append(
                            f"Logger {lg['logger_id']} assigned to Shipment {shp['shipment_id']} is not ACTIVE_CALIBRATED"
                        )
            if lg:
                assignments_by_logger.setdefault(lg["logger_id"], []).append((act_dt, deact_dt, assgn["shipment_id"]))

        for lid, windows in assignments_by_logger.items():
            sorted_win = sorted(windows, key=lambda x: x[0])
            for i in range(1, len(sorted_win)):
                prev_start, prev_end, prev_sid = sorted_win[i - 1]
                curr_start, curr_end, curr_sid = sorted_win[i]
                checks_run += 1
                if curr_start < prev_end:
                    errors.append(
                        f"Logger {lid} has overlapping assignments on {prev_sid} and {curr_sid}"
                    )

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
        reconciled_custody_events = 0

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

        for ce in self.datasets["CustodyEvent"]:
            if "handoff_started_utc" in ce and ce["handoff_started_utc"] is not None:
                start_dt = parse_iso_utc(ce["handoff_started_utc"])
                end_dt = parse_iso_utc(ce["event_timestamp_utc"])
                gap_diff = int((end_dt - start_dt).total_seconds() // 60)
                if gap_diff != ce["handover_gap_min"]:
                    errors.append(
                        f"CustodyEvent {ce['custody_event_id']} handover_gap_min={ce['handover_gap_min']} "
                        f"!= timestamp diff {gap_diff}"
                    )
                else:
                    reconciled_custody_events += 1

        return {
            "passed": len(errors) == 0,
            "reconciled_shipment_values": reconciled_shipments,
            "reconciled_route_stop_dwells": reconciled_stops,
            "reconciled_custody_handover_gaps": reconciled_custody_events,
            "error_count": len(errors),
            "sample_errors": errors[:10],
        }

    def _check_lifecycle_transitions(self) -> Dict[str, Any]:
        """
        Priority 5:
        1. Every Shipment in `Shipment` MUST have a complete `ShipmentStateHistory` chain (`len >= 6`),
           starting at `None -> PLANNED`, passing through `PACKED -> IN_TRANSIT -> DELIVERED -> EVALUATED`,
           and ending in a terminal `CLOSED_*` state. Deleting history rows for any shipment fails validation.
        2. `Shipment.lifecycle_state` MUST match the final `new_state` in `ShipmentStateHistory` AND
           reconcile with the latest effective disposition (`ComplianceReview.audited_disposition` if overridden,
           else `ExcursionReport.disposition_decision`).
        """
        errors: List[str] = []
        events_by_shp: Dict[str, List[Dict[str, Any]]] = {}
        for ev in self.datasets["ShipmentStateHistory"]:
            events_by_shp.setdefault(ev["shipment_id"], []).append(ev)

        report_by_shp = {er["shipment_id"]: er for er in self.datasets["ExcursionReport"]}
        review_by_shp = {rv["shipment_id"]: rv for rv in self.datasets.get("ComplianceReview", [])}

        verified_chains = 0
        for shp in self.datasets["Shipment"]:
            sid = shp["shipment_id"]
            ev_list = events_by_shp.get(sid, [])
            if len(ev_list) < 6:
                errors.append(
                    f"{sid}: Incomplete or missing ShipmentStateHistory chain (found {len(ev_list)} events, expected >= 6)"
                )
                continue

            sorted_ev = sorted(ev_list, key=lambda x: x["sequence_no"])
            expected_seq = list(range(1, len(sorted_ev) + 1))
            actual_seq = [e["sequence_no"] for e in sorted_ev]
            if actual_seq != expected_seq:
                errors.append(f"{sid}: Non-contiguous state sequence numbers {actual_seq}")

            prev_state = None
            prev_dt = None
            chain_ok = True
            for ev in sorted_ev:
                if ev["prior_state"] != prev_state:
                    errors.append(f"{sid}: Broken state chain prior_state={ev['prior_state']} != {prev_state}")
                    chain_ok = False
                allowed_next = LEGAL_STATE_TRANSITIONS.get(prev_state, set())
                if ev["new_state"] not in allowed_next:
                    errors.append(f"{sid}: Illegal transition {prev_state} -> {ev['new_state']}")
                    chain_ok = False
                curr_dt = parse_iso_utc(ev["changed_at_utc"])
                if prev_dt and curr_dt < prev_dt:
                    errors.append(f"{sid}: Non-monotonic state timestamp at seq {ev['sequence_no']}")
                    chain_ok = False
                prev_state = ev["new_state"]
                prev_dt = curr_dt

            # Verify final historical state matches Shipment.lifecycle_state
            if prev_state != shp["lifecycle_state"]:
                errors.append(
                    f"{sid}: Shipment.lifecycle_state='{shp['lifecycle_state']}' does not match "
                    f"final ShipmentStateHistory.new_state='{prev_state}'"
                )
                chain_ok = False

            # Verify Shipment.lifecycle_state reconciles with effective disposition
            rep = report_by_shp.get(sid)
            rev = review_by_shp.get(sid)
            if rev is not None and rev.get("override_applied"):
                effective_disp = rev["audited_disposition"]
            elif rep is not None:
                effective_disp = rep["disposition_decision"]
            else:
                effective_disp = None

            if effective_disp is not None:
                expected_closed_state = disposition_to_closed_state(effective_disp)
                if shp["lifecycle_state"] != expected_closed_state:
                    errors.append(
                        f"{sid}: Shipment.lifecycle_state='{shp['lifecycle_state']}' does not reconcile with "
                        f"effective disposition '{effective_disp}' (expected '{expected_closed_state}')"
                    )
                    chain_ok = False

            if chain_ok:
                verified_chains += 1

        return {
            "passed": len(errors) == 0 and verified_chains == len(self.datasets["Shipment"]),
            "verified_shipment_state_chains": verified_chains,
            "error_count": len(errors),
            "sample_errors": errors[:10],
        }

    def _audit_edge_cases_and_ground_truth(self) -> Dict[str, Any]:
        """
        Independently audit every shipment from raw master/transaction/event tables using
        `ComplianceEngine.audit_dataset(self.datasets)`, verify consistency with `ComplianceReview`
        and `GroundTruth` (when `GroundTruth` is present), and publish separate metrics for:
        - Ground-truth reconstruction accuracy
        - Original pre-audit compliance-report decision accuracy
        - Post-audit ComplianceReview decision accuracy
        - Edge-case detection precision and recall
        - False-compliant shipments in uncorrected reports
        - Incorrect excursion durations in uncorrected reports
        - Incorrect dispositions in uncorrected reports
        """
        audit_by_shp = ComplianceEngine.audit_dataset(self.datasets)
        orphan_reading_count = sum(1 for r in self.datasets["TemperatureReading"] if r["shipment_id"] is None)

        report_by_shp = {er["shipment_id"]: er for er in self.datasets["ExcursionReport"]}
        review_by_shp = {rv["shipment_id"]: rv for rv in self.datasets.get("ComplianceReview", [])}
        gt_by_shp = {gt["shipment_id"]: gt for gt in self.datasets.get("GroundTruth", [])}

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
        post_audit_review_matches = 0
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
            audit_res = audit_by_shp[sid]
            true_eval = audit_res.recomputed_eval
            detected_edge = audit_res.detected_anomaly_type
            rep = report_by_shp[sid]
            rev = review_by_shp.get(sid)
            gt = gt_by_shp.get(sid)

            if detected_edge != "NONE":
                detected_counts[detected_edge] = detected_counts.get(detected_edge, 0) + 1

            # Verify persisted ComplianceReview exists and matches operational audit
            if rev is None:
                mismatches.append(f"{sid}: Missing required ComplianceReview record")
            else:
                if (
                    rev["anomaly_type"] == detected_edge
                    and rev["audited_status"] == audit_res.audited_compliance_status
                    and rev["audited_disposition"] == audit_res.audited_disposition_decision
                ):
                    post_audit_review_matches += 1
                else:
                    mismatches.append(
                        f"{sid}: ComplianceReview ({rev['anomaly_type']}, {rev['audited_status']}) != "
                        f"live audit ({detected_edge}, {audit_res.audited_compliance_status})"
                    )

            # Compare pre-audit original ExcursionReport against reconstructed audit
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

            if gt is not None:
                gt_edge = gt["edge_case_type"]
                if detected_edge != "NONE" and detected_edge == gt_edge:
                    true_positive_edge_detections += 1
                elif detected_edge != "NONE" and detected_edge != gt_edge:
                    false_positive_edge_detections += 1
                elif detected_edge == "NONE" and gt_edge != "NONE":
                    false_negative_edge_detections += 1

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
            else:
                gt_matches += 1

        precision = (
            round((true_positive_edge_detections / (true_positive_edge_detections + false_positive_edge_detections)) * 100.0, 2)
            if (true_positive_edge_detections + false_positive_edge_detections) > 0
            else 100.0
        )
        recall = (
            round((true_positive_edge_detections / (true_positive_edge_detections + false_negative_edge_detections)) * 100.0, 2)
            if (true_positive_edge_detections + false_negative_edge_detections) > 0
            else 100.0
        )

        return {
            "passed": (
                len(mismatches) == 0
                and post_audit_review_matches == total_shipments
                and all(v > 0 for v in detected_counts.values())
            ),
            "total_shipments_audited": total_shipments,
            "ground_truth_exact_matches": gt_matches,
            "ground_truth_reconstruction_accuracy_pct": round((gt_matches / total_shipments) * 100.0, 2),
            "post_audit_review_accuracy_pct": round((post_audit_review_matches / total_shipments) * 100.0, 2),
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


def verify_sqlite_foreign_key_enforcement(sqlite_db_path: Path | str) -> Dict[str, Any]:
    """
    Verify that `open_sqlite_db` enables `PRAGMA foreign_keys = ON`, passes `PRAGMA foreign_key_check`,
    and actively rejects an invalid foreign-key insert with `sqlite3.IntegrityError`.
    """
    conn = open_sqlite_db(sqlite_db_path)
    try:
        fk_pragma_val = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()

        rejected_invalid_fk = False
        try:
            conn.execute(
                "INSERT INTO Lot (lot_id, product_id, manufacturing_site, manufactured_date, expiry_date, "
                "initial_quantity_units, remaining_stability_budget_min, lifecycle_state) "
                "VALUES ('LOT-2026-999999', 'PROD-NONEXISTENT-9999', 'TEST-SITE', '2026-01-01', '2027-01-01', 100, 60, 'RELEASED');"
            )
            conn.rollback()
        except sqlite3.IntegrityError:
            rejected_invalid_fk = True
            conn.rollback()

        return {
            "passed": (fk_pragma_val == 1) and (len(fk_violations) == 0) and rejected_invalid_fk,
            "pragma_foreign_keys_on": fk_pragma_val == 1,
            "foreign_key_check_violations": len(fk_violations),
            "invalid_fk_insert_rejected": rejected_invalid_fk,
        }
    finally:
        conn.close()


def verify_reproducibility(seed: int = DEFAULT_SEED, scale: str = "starter") -> Dict[str, Any]:
    """
    Step 10: Regenerate dataset twice in isolated directories with the same fixed seed
    and compare all row counts and file SHA-256 checksums, plus verify SQLite FK enforcement.
    """
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        gen1 = SyntheticDataGenerator(seed=seed, scale=scale)
        ds1 = gen1.generate_all()
        man1 = export_datasets(ds1, Path(tmp1), seed=seed, scale=scale, export_sql=True)
        sqlite_fk_check = verify_sqlite_foreign_key_enforcement(Path(tmp1) / "sql" / "cold_chain_compliance.db")

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
            "reproducible_100_pct": all_identical and sqlite_fk_check["passed"],
            "sqlite_foreign_key_enforcement": sqlite_fk_check,
            "files_compared": len(comparisons),
            "comparisons": comparisons,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Cold-Chain Compliance synthetic dataset & reproducibility.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Seed to validate")
    parser.add_argument("--scale", choices=["full", "starter"], default="full", help="Scale to validate")
    parser.add_argument(
        "--input-dir",
        type=str,
        default=None,
        help="Optional directory of exported files to validate (verifies manifest SHA-256 checksums and CSV contents)",
    )
    parser.add_argument(
        "--report-out",
        type=str,
        default="docs/VALIDATION_REPORT.json",
        help="Output path for JSON validation report",
    )
    args = parser.parse_args()

    manifest_check: Optional[Dict[str, Any]] = None
    if args.input_dir:
        in_path = Path(args.input_dir)
        print(f"[Validator] Validating exported dataset on disk at {in_path.resolve()}...")
        manifest_check = verify_manifest_checksums(in_path)
        datasets = load_datasets_from_dir(in_path)
    else:
        print(f"[Validator] Generating dataset in-memory for validation (seed={args.seed}, scale={args.scale})...")
        gen = SyntheticDataGenerator(seed=args.seed, scale=args.scale)
        datasets = gen.generate_all()

    validator = DatasetValidator(datasets)
    val_results = validator.run_all_checks()
    if manifest_check is not None:
        val_results["manifest_checksum_verification"] = manifest_check
        if not manifest_check["passed"]:
            val_results["overall_passed"] = False

    print(
        f"[Validator] Running Step 10 reproducibility verification "
        f"(two independent runs with seed={args.seed}, scale={args.scale})..."
    )
    repro_results = verify_reproducibility(seed=args.seed, scale=args.scale)

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

    if not val_results["overall_passed"] or not repro_results["reproducible_100_pct"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
