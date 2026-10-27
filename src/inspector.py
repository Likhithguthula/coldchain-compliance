"""
Interactive & Command-Line Shipment Compliance Inspector for Logistics Analysts
and Operations Managers.

Allows reviewers, logistics analysts, and operations managers to:
1. View a portfolio-wide executive dashboard summary of all shipments, compliance statuses,
   disposition decisions, and detected process anomalies.
2. List and filter all exception / quarantine / negative-edge-case shipments.
3. Inspect any individual shipment (e.g., `--shipment-id SHP-2026-000018`) end-to-end:
   - Master Product, Lot(s), Packaging, and ThresholdPolicy vs. Canonical Policy
   - Assigned Logger(s) and Calibration Uncertainty
   - Route Stops and Dwell Events
   - Chain-of-Custody Handoffs and Delays
   - 10-Minute Temperature Readings and First Breach Point
   - Pre-Audit ExcursionReport vs. Independent Audit Reconstruction
   - Immutable ShipmentStateHistory Lifecycle Audit Trail

Usage:
    python -m src.inspector --scale starter
    python -m src.inspector --scale starter --list-exceptions
    python -m src.inspector --scale starter --shipment-id SHP-2026-000018
"""

from __future__ import annotations

import argparse
from collections import Counter
from typing import Any, Dict, List

from src.compliance_engine import ComplianceEngine, PolicyThresholds, ReadingPoint
from src.generator import DEFAULT_SEED, SyntheticDataGenerator


def build_policy_map(datasets: Dict[str, List[Dict[str, Any]]]) -> Dict[str, PolicyThresholds]:
    pol_map: Dict[str, PolicyThresholds] = {}
    for p in datasets["ThresholdPolicy"]:
        pol_map[p["policy_id"]] = PolicyThresholds(
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
            max_custody_gap_min=p.get("max_custody_gap_min", 120),
            effective_from=p.get("effective_from", "2026-01-01T00:00:00Z"),
            effective_to=p.get("effective_to"),
        )
    return pol_map


def print_portfolio_summary(datasets: Dict[str, List[Dict[str, Any]]], seed: int, scale: str) -> None:
    shipments = datasets["Shipment"]
    reports = datasets["ExcursionReport"]
    gt_rows = datasets["GroundTruth"]

    rep_status = Counter(r["compliance_status"] for r in reports)
    rep_disp = Counter(r["disposition_decision"] for r in reports)
    gt_edges = Counter(g["edge_case_type"] for g in gt_rows if g["edge_case_type"] != "NONE")

    print("=" * 80)
    print(f"YOTTAFLEX OPERATIONS MANAGER & ANALYST DASHBOARD (seed={seed}, scale={scale})")
    print("=" * 80)
    print(f"Total Shipments Monitored : {len(shipments):,}")
    print(f"Total Loggers Active      : {len(datasets['Logger']):,}")
    print(f"Total Telemetry Readings  : {len(datasets['TemperatureReading']):,}")
    print("-" * 80)
    print("Reported Compliance Status Breakdown:")
    for st, cnt in sorted(rep_status.items(), key=lambda x: (-x[1], x[0])):
        print(f"  - {st:<32}: {cnt:>6,} ({cnt / len(reports) * 100:5.1f}%)")
    print("-" * 80)
    print("Disposition Decisions:")
    for dp, cnt in sorted(rep_disp.items(), key=lambda x: (-x[1], x[0])):
        print(f"  - {dp:<32}: {cnt:>6,} ({cnt / len(reports) * 100:5.1f}%)")
    print("-" * 80)
    print("Audited Process / Anomaly Edge Cases Flagged for QA Override:")
    for ed, cnt in sorted(gt_edges.items()):
        print(f"  - {ed:<32}: {cnt:>6,} shipments")
    print("=" * 80)


def list_exception_shipments(datasets: Dict[str, List[Dict[str, Any]]], limit: int = 20) -> None:
    gt_rows = [g for g in datasets["GroundTruth"] if g["true_compliance_status"] != "COMPLIANT" or g["is_injected_edge_case"]]
    print(f"\nShowing first {min(limit, len(gt_rows))} of {len(gt_rows)} Exception / Anomaly Shipments:")
    print("-" * 108)
    print(f"{'Shipment ID':<16} | {'Scenario Category':<34} | {'Reported Status':<26} | {'True Audited Status':<26}")
    print("-" * 108)
    for g in gt_rows[:limit]:
        print(
            f"{g['shipment_id']:<16} | {g['scenario_category']:<34} | "
            f"{g['reported_compliance_status']:<26} | {g['true_compliance_status']:<26}"
        )
    print("-" * 108)


def inspect_shipment(datasets: Dict[str, List[Dict[str, Any]]], shipment_id: str) -> None:
    shp = next((s for s in datasets["Shipment"] if s["shipment_id"] == shipment_id), None)
    if not shp:
        raise ValueError(f"Shipment '{shipment_id}' not found in dataset.")

    prod = next(p for p in datasets["Product"] if p["product_id"] == shp["primary_product_id"])
    pkg = next(pk for pk in datasets["Packaging"] if pk["packaging_id"] == shp["packaging_id"])
    rep = next(r for r in datasets["ExcursionReport"] if r["shipment_id"] == shipment_id)
    gt = next(g for g in datasets["GroundTruth"] if g["shipment_id"] == shipment_id)
    pol_map = build_policy_map(datasets)
    assigned_pol = pol_map[shp["assigned_policy_id"]]
    true_pol = next(p for p in pol_map.values() if p.product_id == prod["product_id"])

    shp_lots = [sl for sl in datasets["ShipmentLot"] if sl["shipment_id"] == shipment_id]
    shp_loggers = [sl for sl in datasets["ShipmentLogger"] if sl["shipment_id"] == shipment_id]
    stops = [st for st in datasets["RouteStop"] if st["shipment_id"] == shipment_id]
    custody = [ce for ce in datasets["CustodyEvent"] if ce["shipment_id"] == shipment_id]
    readings = [tr for tr in datasets["TemperatureReading"] if tr["shipment_id"] == shipment_id]
    history = [sh for sh in datasets["ShipmentStateHistory"] if sh["shipment_id"] == shipment_id]

    print("=" * 88)
    print(f"SHIPMENT TRACEABILITY DOSSIER: {shipment_id}")
    print("=" * 88)
    print(f"Product           : {prod['product_id']} ({prod['name']}) | Regime: {prod['storage_regime']}")
    print(f"Packaging         : {pkg['packaging_id']} ({pkg['model_name']}) | Hold: {pkg['validated_hold_hours']}h")
    print(f"Route & Mode      : {shp['origin_location']} -> {shp['destination_location']} ({shp['transport_mode']})")
    print(f"Actual Window     : {shp['actual_departure_utc']} to {shp['actual_arrival_utc']}")
    print(f"Declared Value    : ${shp['declared_value_usd']:,.2f} across {len(shp_lots)} lot(s)")
    print(f"Assigned Policy   : {assigned_pol.policy_id} ([{assigned_pol.min_temp_c}C, {assigned_pol.max_temp_c}C])")
    print(f"Canonical Policy  : {true_pol.policy_id} ([{true_pol.min_temp_c}C, {true_pol.max_temp_c}C])")
    print(f"Assigned Loggers  : {[sl['logger_id'] for sl in shp_loggers] or 'NONE (UNASSIGNED DEFECT)'}")
    print("-" * 88)
    print("Route Stops & Dwell Events:")
    for st in stops:
        print(
            f"  [{st['sequence_no']}] {st['stop_id']} | {st['location_code']:<25} | "
            f"Dwell: {st['dwell_duration_min']:>3} min | Flag: {st['dwell_event_flag']}"
        )
    print("-" * 88)
    print("Chain-of-Custody Events:")
    for ce in custody:
        print(
            f"  [{ce['sequence_no']}] {ce['custody_event_id']} | {ce['event_type']:<18} | "
            f"{ce['from_actor_role']} -> {ce['to_actor_role']} | Gap: {ce['handover_gap_min']} min "
            f"(Flagged: {ce['custody_gap_flagged']})"
        )
    print("-" * 88)
    print(f"Telemetry Summary ({len(readings)} linked readings, Expected: {rep['expected_reading_count']}):")
    print(
        f"  Min Temp: {rep['min_recorded_temp_c']}C | Max Temp: {rep['max_recorded_temp_c']}C | "
        f"MKT: {rep['mean_kinetic_temp_c']}C | First Breach ID: {rep['first_breach_reading_id']}"
    )
    print("-" * 88)
    print("Compliance Report vs. Independent QA Audit:")
    print(f"  Pre-Audit ExcursionReport Status : {rep['compliance_status']} ({rep['disposition_decision']})")
    print(f"  Independent QA Audited Status    : {gt['true_compliance_status']} ({gt['true_disposition_decision']})")
    print(f"  Injected Edge Case Type          : {gt['edge_case_type']}")
    print(f"  Root Cause Summary               : {gt['root_cause_summary']}")
    print("-" * 88)
    print("Immutable Lifecycle State History:")
    for sh in history:
        print(
            f"  [{sh['sequence_no']}] {sh['changed_at_utc']} | {str(sh['prior_state']):<10} -> "
            f"{sh['new_state']:<18} | Actor: {sh['actor_role']:<18} | {sh['reason_code']}"
        )
    print("=" * 88)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Cold-Chain Shipments, Exceptions, and Audit Trails.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Generator seed (default: 20261022)")
    parser.add_argument("--scale", choices=["starter", "full"], default="starter", help="Dataset scale")
    parser.add_argument("--list-exceptions", action="store_true", help="List non-compliant and anomaly shipments")
    parser.add_argument("--shipment-id", type=str, default=None, help="Inspect a specific shipment ID (e.g. SHP-2026-000018)")
    args = parser.parse_args()

    gen = SyntheticDataGenerator(seed=args.seed, scale=args.scale)
    datasets = gen.generate_all()

    if args.shipment_id:
        inspect_shipment(datasets, args.shipment_id)
    else:
        print_portfolio_summary(datasets, seed=args.seed, scale=args.scale)
        if args.list_exceptions:
            list_exception_shipments(datasets)


if __name__ == "__main__":
    main()
