"""
Core Cold-Chain Compliance & Deviation Evaluation Engine.

Provides deterministic, traceable rules for:
1. Mean Kinetic Temperature (MKT) calculation via the Haynes / USP <1079> equation.
2. Bounded excursion duration calculation using piecewise-constant hold on [t_i, t_{i+1})
   strictly clipped to the shipment window [actual_departure_utc, actual_arrival_utc], so
   excursion duration can never exceed total trip duration.
3. Strict expected 10-minute time-grid verification, off-grid timestamp detection,
   duplicate timestamp / reading_id detection, and non-finite (NaN/Inf) telemetry rejection.
4. Product-to-Policy compatibility and effective-date window verification.
5. Configurable custody handoff delay / gap verification.
6. Calibration uncertainty borderline and excursion-across-gap uncertainty detection.
7. Independent Operational QA Audit (`audit_shipment` / `audit_dataset`) operating purely
   on master/transaction/event records without reading GroundTruth labels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple


# Constants for Haynes Mean Kinetic Temperature (MKT) formula (USP <1079>)
DELTA_H_KJ_MOL = 83.14472  # Activation energy in kJ/mol
GAS_CONSTANT_R = 0.008314472  # Universal gas constant in kJ/(mol*K)
KELVIN_OFFSET = 273.15
CUSTODY_GAP_THRESHOLD_MIN = 120  # Default maximum allowed custody handoff delay in minutes


@dataclass(frozen=True)
class PolicyThresholds:
    policy_id: str
    product_id: str
    min_temp_c: float
    max_temp_c: float
    critical_low_temp_c: float
    critical_high_temp_c: float
    max_single_excursion_min: int
    max_cumulative_excursion_min: int
    max_mkt_c: float
    max_telemetry_gap_min: int
    max_custody_gap_min: int = CUSTODY_GAP_THRESHOLD_MIN
    effective_from: str = "2026-01-01T00:00:00Z"
    effective_to: Optional[str] = None
    version: int = 1


@dataclass(frozen=True)
class ReadingPoint:
    reading_id: str
    logger_id: str
    shipment_id: Optional[str]
    sequence_no: int
    recorded_at_utc: str
    temperature_c: float
    calibration_uncertainty_c: float
    elapsed_since_prev_min: int


@dataclass(frozen=True)
class EvaluationResult:
    expected_reading_count: int
    actual_reading_count: int
    missing_reading_count: int
    max_telemetry_gap_min: int
    telemetry_gap_flagged: bool
    custody_gap_flagged: bool
    min_recorded_temp_c: Optional[float]
    max_recorded_temp_c: Optional[float]
    mean_kinetic_temp_c: Optional[float]
    total_excursion_duration_min: int
    max_single_excursion_min: int
    uncertainty_borderline_flag: bool
    compliance_status: str
    disposition_decision: str
    first_breach_reading_id: Optional[str]
    evaluated_by_actor: str


@dataclass(frozen=True)
class OperationalAuditResult:
    shipment_id: str
    report_id: str
    canonical_policy_id: str
    assigned_policy_id: str
    detected_anomaly_type: str
    has_operational_defect: bool
    recomputed_eval: EvaluationResult
    reported_compliance_status: str
    reported_disposition_decision: str
    audited_compliance_status: str
    audited_disposition_decision: str
    requires_corrective_override: bool
    evidence_ids: str
    audit_summary: str


def parse_iso_utc(ts_str: str) -> datetime:
    """Parse ISO-8601 UTC timestamp string (YYYY-MM-DDTHH:MM:SSZ)."""
    if not ts_str or not isinstance(ts_str, str):
        raise ValueError(f"Invalid ISO timestamp: {ts_str!r}")
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp must include UTC timezone offset or 'Z': {ts_str!r}")
    return dt


def compute_mkt_celsius(temperatures_c: List[float]) -> Optional[float]:
    """
    Calculate Mean Kinetic Temperature (MKT) in degrees Celsius using Haynes formula:
        MKT (K) = (DeltaH / R) / -ln( (1/N) * sum( exp(-DeltaH / (R * T_i)) ) )
    """
    if not temperatures_c:
        return None
    for t in temperatures_c:
        if not isinstance(t, (int, float)) or not math.isfinite(t) or (t + KELVIN_OFFSET) <= 0:
            raise ValueError(f"Non-finite or unphysical temperature in MKT calculation: {t!r}")

    ratio = DELTA_H_KJ_MOL / GAS_CONSTANT_R  # 10,000 K
    exponents = [-ratio / (t + KELVIN_OFFSET) for t in temperatures_c]
    max_exp = max(exponents)
    sum_shifted = sum(math.exp(e - max_exp) for e in exponents)
    mean_log = max_exp + math.log(sum_shifted / len(temperatures_c))
    mkt_kelvin = -ratio / mean_log
    return round(mkt_kelvin - KELVIN_OFFSET, 2)


def select_effective_policy_for_product(
    policies: List[PolicyThresholds],
    product_id: str,
    departure_utc: str,
    arrival_utc: str,
) -> PolicyThresholds:
    """
    Select the canonical ThresholdPolicy for `product_id` whose effective window
    `[effective_from, effective_to]` covers `[departure_utc, arrival_utc]`.
    If multiple versions match, select the highest `version`.
    """
    dep_dt = parse_iso_utc(departure_utc)
    arr_dt = parse_iso_utc(arrival_utc)
    candidates: List[PolicyThresholds] = []
    fallback: List[PolicyThresholds] = []

    for pol in policies:
        if pol.product_id != product_id:
            continue
        fallback.append(pol)
        eff_from = parse_iso_utc(pol.effective_from) if pol.effective_from else None
        eff_to = parse_iso_utc(pol.effective_to) if pol.effective_to else None
        if eff_from and dep_dt < eff_from:
            continue
        if eff_to and arr_dt > eff_to:
            continue
        candidates.append(pol)

    if candidates:
        return sorted(candidates, key=lambda p: p.version, reverse=True)[0]
    if fallback:
        return sorted(fallback, key=lambda p: p.version, reverse=True)[0]
    raise KeyError(f"No ThresholdPolicy found for product_id={product_id!r}")


class ComplianceEngine:
    """Deterministic business logic evaluator for temperature-sensitive shipments."""

    @staticmethod
    def evaluate_shipment(
        actual_departure_utc: str,
        actual_arrival_utc: str,
        policy: PolicyThresholds,
        readings: List[ReadingPoint],
        custody_handover_gaps_min: List[int],
        has_assigned_logger: bool = True,
        sampling_interval_min: int = 10,
        expected_product_id: Optional[str] = None,
        expected_shipment_id: Optional[str] = None,
        expected_logger_ids: Optional[Set[str]] = None,
    ) -> EvaluationResult:
        """
        Evaluate a shipment's temperature telemetry and custody chain against its policy.

        Interval & Excursion Duration Convention (Priority 3 Fix):
        - Each reading at timestamp `t_i` holds piecewise-constant over `[t_i, t_next)`,
          where `t_next = min(arr_dt, t_{i+1})` for `i < N-1` and `t_next = arr_dt` for the final reading.
        - Because disjoint sub-intervals `[t_i, t_next)` partition `[t_0, arr_dt] subset [dep_dt, arr_dt]`,
          total excursion duration is strictly bounded by `[0, trip_minutes]`.
        - For a single isolated spike on a regular 10-minute grid (`t_i` breaching, `t_{i+1} = t_i + 10m` normal),
          the held interval `[t_i, t_{i+1})` is 10 minutes. For a terminal reading at `t_i == arr_dt`,
          if `t_i` is the only breach at the exact arrival instant (`[arr_dt, arr_dt)`), its held duration
          is `0` unless the preceding interval was absent; if a zero-duration trip (`dep_dt == arr_dt`) occurs,
          excursion duration is `0` minutes.
        """
        if not isinstance(sampling_interval_min, int) or sampling_interval_min <= 0:
            raise ValueError(f"sampling_interval_min must be a positive integer, got {sampling_interval_min!r}")

        dep_dt = parse_iso_utc(actual_departure_utc)
        arr_dt = parse_iso_utc(actual_arrival_utc)
        if arr_dt < dep_dt:
            raise ValueError("actual_arrival_utc cannot be earlier than actual_departure_utc")

        trip_minutes = max(0, int((arr_dt - dep_dt).total_seconds() // 60))
        expected_count = (trip_minutes // sampling_interval_min) + 1

        # Build the exact expected timestamp grid from departure to arrival
        expected_grid_dts: Set[datetime] = {
            dep_dt + timedelta(minutes=i * sampling_interval_min)
            for i in range(expected_count)
        }

        # Verify custody handoff gaps against configurable policy threshold
        custody_limit_min = getattr(policy, "max_custody_gap_min", CUSTODY_GAP_THRESHOLD_MIN)
        for g in custody_handover_gaps_min:
            if not isinstance(g, (int, float)) or not math.isfinite(g) or g < 0:
                raise ValueError(f"Invalid custody handover gap value: {g!r}")
        max_custody_gap = int(max(custody_handover_gaps_min)) if custody_handover_gaps_min else 0
        custody_gap_flagged = max_custody_gap > custody_limit_min

        # Verify Product-to-Policy compatibility and effective window (Bug 4)
        policy_mismatch_detected = False
        if expected_product_id is not None and policy.product_id != expected_product_id:
            policy_mismatch_detected = True

        pol_eff_from_str = getattr(policy, "effective_from", None)
        pol_eff_to_str = getattr(policy, "effective_to", None)
        if pol_eff_from_str:
            eff_from_dt = parse_iso_utc(pol_eff_from_str)
            if dep_dt < eff_from_dt:
                policy_mismatch_detected = True
        if pol_eff_to_str:
            eff_to_dt = parse_iso_utc(pol_eff_to_str)
            if arr_dt > eff_to_dt:
                policy_mismatch_detected = True

        # Inspect readings for non-finite values, cross-shipment contamination,
        # outside-window timestamps, off-grid timestamps, and duplicates
        invalid_telemetry_detected = False
        duplicate_detected = False
        off_grid_detected = False
        contamination_or_out_of_window_detected = False
        seen_reading_ids: Set[str] = set()
        seen_timestamps_by_logger: Set[Tuple[str, datetime]] = set()
        valid_readings_by_dt: Dict[datetime, ReadingPoint] = {}

        for r in readings:
            # Reject non-finite (NaN / Inf) temperatures or uncertainties immediately
            if (
                not isinstance(r.temperature_c, (int, float))
                or not math.isfinite(r.temperature_c)
                or not isinstance(r.calibration_uncertainty_c, (int, float))
                or not math.isfinite(r.calibration_uncertainty_c)
                or r.calibration_uncertainty_c < 0
            ):
                invalid_telemetry_detected = True
                continue

            if r.reading_id in seen_reading_ids:
                duplicate_detected = True
                continue
            seen_reading_ids.add(r.reading_id)

            if expected_shipment_id is not None and r.shipment_id != expected_shipment_id:
                contamination_or_out_of_window_detected = True
                continue
            if r.shipment_id is None:
                contamination_or_out_of_window_detected = True
                continue
            if expected_logger_ids is not None and r.logger_id not in expected_logger_ids:
                contamination_or_out_of_window_detected = True
                continue

            r_dt = parse_iso_utc(r.recorded_at_utc)

            key = (r.logger_id, r_dt)
            if key in seen_timestamps_by_logger or r_dt in valid_readings_by_dt:
                duplicate_detected = True
                continue
            seen_timestamps_by_logger.add(key)

            if r_dt < dep_dt or r_dt > arr_dt:
                contamination_or_out_of_window_detected = True
                continue

            if r_dt not in expected_grid_dts:
                off_grid_detected = True

            valid_readings_by_dt[r_dt] = r

        # Case 1: No assigned logger, invalid NaN/Inf telemetry, or zero valid readings available
        if not has_assigned_logger or invalid_telemetry_detected or len(valid_readings_by_dt) == 0:
            return EvaluationResult(
                expected_reading_count=expected_count,
                actual_reading_count=len(valid_readings_by_dt) if has_assigned_logger else 0,
                missing_reading_count=expected_count,
                max_telemetry_gap_min=trip_minutes,
                telemetry_gap_flagged=True,
                custody_gap_flagged=custody_gap_flagged,
                min_recorded_temp_c=None,
                max_recorded_temp_c=None,
                mean_kinetic_temp_c=None,
                total_excursion_duration_min=0,
                max_single_excursion_min=0,
                uncertainty_borderline_flag=False,
                compliance_status="NON_COMPLIANT_MISSING_DATA",
                disposition_decision="QUARANTINE_HOLD",
                first_breach_reading_id=None,
                evaluated_by_actor="OPERATIONS_MANAGER",
            )

        # Sort deduplicated, in-window readings chronologically
        sorted_dts = sorted(valid_readings_by_dt.keys())
        sorted_readings = [valid_readings_by_dt[dt] for dt in sorted_dts]
        actual_count = len(sorted_readings)

        # Calculate missing readings strictly from the expected time grid (Priority 2 Fix)
        covered_grid_points = sum(1 for g_dt in expected_grid_dts if g_dt in valid_readings_by_dt)
        missing_count = max(0, expected_count - covered_grid_points)

        # Compute telemetry gaps from both consecutive readings AND uncovered grid spans
        max_gap_min = 0
        first_dt = sorted_dts[0]
        initial_gap = max(0, int((first_dt - dep_dt).total_seconds() // 60))
        if initial_gap > max_gap_min:
            max_gap_min = initial_gap

        for idx in range(1, actual_count):
            prev_dt = sorted_dts[idx - 1]
            curr_dt = sorted_dts[idx]
            delta_min = int((curr_dt - prev_dt).total_seconds() // 60)
            missing_window = max(0, delta_min - sampling_interval_min)
            if missing_window > max_gap_min:
                max_gap_min = missing_window

        last_dt = sorted_dts[-1]
        tail_gap = max(0, int((arr_dt - last_dt).total_seconds() // 60))
        if tail_gap > max_gap_min:
            max_gap_min = tail_gap

        # Also check gap between covered points on the expected grid so off-grid readings
        # (e.g. at minutes 0, 5, 15, 20 when minute 10 is missing) cannot mask a missing grid slot!
        covered_grid_sorted = sorted(g_dt for g_dt in expected_grid_dts if g_dt in valid_readings_by_dt)
        if covered_grid_sorted:
            grid_init_gap = max(0, int((covered_grid_sorted[0] - dep_dt).total_seconds() // 60))
            max_gap_min = max(max_gap_min, grid_init_gap)
            for idx in range(1, len(covered_grid_sorted)):
                g_delta = int((covered_grid_sorted[idx] - covered_grid_sorted[idx - 1]).total_seconds() // 60)
                max_gap_min = max(max_gap_min, max(0, g_delta - sampling_interval_min))
            grid_tail_gap = max(0, int((arr_dt - covered_grid_sorted[-1]).total_seconds() // 60))
            max_gap_min = max(max_gap_min, grid_tail_gap)
        else:
            max_gap_min = trip_minutes

        # Priority 2 Fix: Under our strict 10-minute grid contract, ANY missing expected grid point
        # (`missing_count > 0`), off-grid reading, duplicate timestamp, or gap >= max_telemetry_gap_min
        # flags insufficient telemetry evidence.
        telemetry_gap_flagged = (
            max_gap_min >= policy.max_telemetry_gap_min
            or missing_count > 0
            or duplicate_detected
            or off_grid_detected
        )

        # Temperature summary statistics
        temps = [r.temperature_c for r in sorted_readings]
        min_temp = round(min(temps), 2)
        max_temp = round(max(temps), 2)
        mkt_c = compute_mkt_celsius(temps)

        # Priority 3 Fix: Bounded Excursion Duration Calculation on [dep_dt, arr_dt]
        # Each reading `i` at `t_i` holds over the disjoint interval `[t_i, t_next)` where
        # `t_next = sorted_dts[i + 1]` for `i < actual_count - 1` and `t_next = arr_dt` for the last reading.
        # This guarantees that the sum of all interval durations never exceeds `trip_minutes`.
        total_excursion_min = 0
        max_single_excursion_min = 0
        current_contiguous_excursion_min = 0
        first_breach_id: Optional[str] = None
        critical_peak_breach = False
        uncertainty_borderline_flag = False
        excursion_across_gap_uncertainty = False

        for idx, r in enumerate(sorted_readings):
            t = r.temperature_c
            unc = r.calibration_uncertainty_c
            is_breach = (t < policy.min_temp_c) or (t > policy.max_temp_c)
            is_critical_peak = (t <= policy.critical_low_temp_c) or (t >= policy.critical_high_temp_c)

            # Calibration uncertainty borderline rule:
            # Flag when a reading is strictly inside the threshold boundary (0 < distance <= unc).
            # Exact threshold values (distance == 0) are treated as inclusive compliant boundary points
            # per documented policy convention.
            dist_low = abs(t - policy.min_temp_c)
            dist_high = abs(t - policy.max_temp_c)
            if (0.0 < dist_low <= unc) or (0.0 < dist_high <= unc):
                uncertainty_borderline_flag = True

            t_start = max(dep_dt, sorted_dts[idx])
            t_end = min(arr_dt, sorted_dts[idx + 1] if (idx + 1) < actual_count else arr_dt)
            interval_min = max(0, int((t_end - t_start).total_seconds() // 60))

            # Check if there is an irregular / missing gap before or after this reading
            prev_delta_min = (
                int((sorted_dts[idx] - sorted_dts[idx - 1]).total_seconds() // 60)
                if idx > 0
                else int((sorted_dts[0] - dep_dt).total_seconds() // 60)
            )
            has_adjacent_gap = (interval_min > sampling_interval_min) or (
                idx > 0 and prev_delta_min > sampling_interval_min
            ) or (idx == 0 and prev_delta_min > 0)

            if is_breach:
                if first_breach_id is None:
                    first_breach_id = r.reading_id
                if is_critical_peak:
                    critical_peak_breach = True

                if has_adjacent_gap or interval_min == 0:
                    excursion_across_gap_uncertainty = has_adjacent_gap
                    uncertainty_borderline_flag = True

                # If the breach occurs only at the final arrival instant `t_start == arr_dt` (`interval_min == 0`),
                # and the previous interval was a gap where the prior reading was also in breach, the gap duration
                # was already accrued on the previous reading's hold `[t_{idx-1}, t_idx)`.
                total_excursion_min += interval_min
                current_contiguous_excursion_min += interval_min
                if current_contiguous_excursion_min > max_single_excursion_min:
                    max_single_excursion_min = current_contiguous_excursion_min
            else:
                if idx > 0 and prev_delta_min > sampling_interval_min:
                    prev_t = sorted_readings[idx - 1].temperature_c
                    if prev_t < policy.min_temp_c or prev_t > policy.max_temp_c:
                        excursion_across_gap_uncertainty = True
                        uncertainty_borderline_flag = True
                current_contiguous_excursion_min = 0

        # Ensure excursion durations never exceed total trip duration
        total_excursion_min = min(trip_minutes, total_excursion_min)
        max_single_excursion_min = min(trip_minutes, max_single_excursion_min)

        mkt_breach = mkt_c is not None and mkt_c > policy.max_mkt_c

        # Decision tree for compliance status and disposition decision (priority ordered)
        if policy_mismatch_detected or contamination_or_out_of_window_detected:
            compliance_status = "INVESTIGATION_REQUIRED"
            disposition_decision = "QUARANTINE_HOLD"
            actor = "OPERATIONS_MANAGER"
        elif (
            critical_peak_breach
            or total_excursion_min > policy.max_cumulative_excursion_min
            or max_single_excursion_min > policy.max_single_excursion_min
            or mkt_breach
        ):
            compliance_status = "CRITICAL_EXCURSION"
            if critical_peak_breach or total_excursion_min > policy.max_cumulative_excursion_min * 2:
                disposition_decision = "REJECT_DESTROY"
            else:
                disposition_decision = "QUARANTINE_HOLD"
            actor = "OPERATIONS_MANAGER"
        elif telemetry_gap_flagged or excursion_across_gap_uncertainty:
            compliance_status = "NON_COMPLIANT_MISSING_DATA"
            disposition_decision = "QUARANTINE_HOLD"
            actor = "LOGISTICS_ANALYST"
        elif custody_gap_flagged:
            compliance_status = "NON_COMPLIANT_CUSTODY_GAP"
            disposition_decision = "QUARANTINE_HOLD"
            actor = "LOGISTICS_ANALYST"
        elif total_excursion_min > 0:
            if uncertainty_borderline_flag:
                compliance_status = "INVESTIGATION_REQUIRED"
                disposition_decision = "QA_MANUAL_REVIEW"
                actor = "LOGISTICS_ANALYST"
            else:
                compliance_status = "MINOR_EXCURSION_ACCEPTABLE"
                disposition_decision = "RELEASE_WITH_STABILITY_DEDUCTION"
                actor = "LOGISTICS_ANALYST"
        elif uncertainty_borderline_flag:
            compliance_status = "INVESTIGATION_REQUIRED"
            disposition_decision = "QA_MANUAL_REVIEW"
            actor = "LOGISTICS_ANALYST"
        else:
            compliance_status = "COMPLIANT"
            disposition_decision = "AUTO_RELEASE"
            actor = "AUTOMATED_POLICY_ENGINE"

        return EvaluationResult(
            expected_reading_count=expected_count,
            actual_reading_count=actual_count,
            missing_reading_count=missing_count,
            max_telemetry_gap_min=max_gap_min,
            telemetry_gap_flagged=telemetry_gap_flagged,
            custody_gap_flagged=custody_gap_flagged,
            min_recorded_temp_c=min_temp,
            max_recorded_temp_c=max_temp,
            mean_kinetic_temp_c=mkt_c,
            total_excursion_duration_min=total_excursion_min,
            max_single_excursion_min=max_single_excursion_min,
            uncertainty_borderline_flag=uncertainty_borderline_flag,
            compliance_status=compliance_status,
            disposition_decision=disposition_decision,
            first_breach_reading_id=first_breach_id,
            evaluated_by_actor=actor,
        )

    @staticmethod
    def audit_shipment_record(
        shipment: Dict[str, Any],
        report: Dict[str, Any],
        policies: List[PolicyThresholds],
        assigned_logger_ids: List[str],
        readings: List[ReadingPoint],
        custody_events: List[Dict[str, Any]],
        assigned_logger_rows: Optional[List[Dict[str, Any]]] = None,
        assigned_assignment_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> OperationalAuditResult:
        """
        Priority 1 Fix: Pure Operational QA Audit of a single shipment.
        Reads ONLY master/transaction/event records and the original ExcursionReport.
        NEVER accesses GroundTruth.
        Also verifies physical logger calibration validity (`calibration_date <= departure_date`,
        `calibration_expiry_date >= arrival_date`, `lifecycle_state == 'ACTIVE_CALIBRATED'`) and
        assignment activation window coverage (`activated_utc <= actual_departure_utc`,
        `deactivated_utc >= actual_arrival_utc`). Any invalid/expired calibration routes the shipment
        to `INVESTIGATION_REQUIRED` / `QUARANTINE_HOLD`.
        """
        sid = shipment["shipment_id"]
        true_pol = select_effective_policy_for_product(
            policies=policies,
            product_id=shipment["primary_product_id"],
            departure_utc=shipment["actual_departure_utc"],
            arrival_utc=shipment["actual_arrival_utc"],
        )

        # Verify assigned logger calibration and activation windows
        calibration_invalid = False
        calibration_evidence: List[str] = []
        dep_dt = parse_iso_utc(shipment["actual_departure_utc"])
        arr_dt = parse_iso_utc(shipment["actual_arrival_utc"])
        dep_date = dep_dt.date().isoformat()
        arr_date = arr_dt.date().isoformat()

        if assigned_logger_rows is not None:
            if len(assigned_logger_rows) < len(assigned_logger_ids):
                calibration_invalid = True
                calibration_evidence.append("MISSING_LOGGER_MASTER_RECORD")
            for lg in assigned_logger_rows:
                cal_from = str(lg.get("calibration_date", ""))
                cal_exp = str(lg.get("calibration_expiry_date", ""))
                lg_state = str(lg.get("lifecycle_state", ""))
                if (
                    not cal_from
                    or not cal_exp
                    or cal_from >= cal_exp
                    or cal_from > dep_date
                    or cal_exp < arr_date
                    or lg_state != "ACTIVE_CALIBRATED"
                ):
                    calibration_invalid = True
                    calibration_evidence.append(
                        f"INVALID_LOGGER_CALIBRATION:{lg.get('logger_id')}[{cal_from}..{cal_exp},{lg_state}]"
                    )

        if assigned_assignment_rows is not None:
            for assgn in assigned_assignment_rows:
                act_dt = parse_iso_utc(assgn["activated_utc"])
                deact_dt = parse_iso_utc(assgn["deactivated_utc"])
                if act_dt >= deact_dt or act_dt > dep_dt or deact_dt < arr_dt:
                    calibration_invalid = True
                    calibration_evidence.append(
                        f"INVALID_ASSIGNMENT_WINDOW:{assgn.get('assignment_id')}"
                    )

        # Compute custody gaps from handoff start/end timestamps if present, or handover_gap_min
        custody_gaps: List[int] = []
        flagged_custody_ids: List[str] = []
        for ce in custody_events:
            if ce.get("handoff_started_utc") and ce.get("event_timestamp_utc"):
                st_dt = parse_iso_utc(ce["handoff_started_utc"])
                en_dt = parse_iso_utc(ce["event_timestamp_utc"])
                gap_val = max(0, int((en_dt - st_dt).total_seconds() // 60))
            else:
                gap_val = int(ce["handover_gap_min"])
            custody_gaps.append(gap_val)
            if gap_val > true_pol.max_custody_gap_min:
                flagged_custody_ids.append(ce["custody_event_id"])

        recomputed_eval = ComplianceEngine.evaluate_shipment(
            actual_departure_utc=shipment["actual_departure_utc"],
            actual_arrival_utc=shipment["actual_arrival_utc"],
            policy=true_pol,
            readings=readings,
            custody_handover_gaps_min=custody_gaps,
            has_assigned_logger=len(assigned_logger_ids) > 0,
            sampling_interval_min=10,
            expected_product_id=shipment["primary_product_id"],
            expected_shipment_id=sid,
            expected_logger_ids=set(assigned_logger_ids) if assigned_logger_ids else None,
        )

        # If logger calibration or assignment coverage is invalid/expired, force INVESTIGATION_REQUIRED / QUARANTINE_HOLD
        if calibration_invalid:
            recomputed_eval = EvaluationResult(
                expected_reading_count=recomputed_eval.expected_reading_count,
                actual_reading_count=recomputed_eval.actual_reading_count,
                missing_reading_count=recomputed_eval.missing_reading_count,
                max_telemetry_gap_min=recomputed_eval.max_telemetry_gap_min,
                telemetry_gap_flagged=recomputed_eval.telemetry_gap_flagged,
                custody_gap_flagged=recomputed_eval.custody_gap_flagged,
                min_recorded_temp_c=recomputed_eval.min_recorded_temp_c,
                max_recorded_temp_c=recomputed_eval.max_recorded_temp_c,
                mean_kinetic_temp_c=recomputed_eval.mean_kinetic_temp_c,
                total_excursion_duration_min=recomputed_eval.total_excursion_duration_min,
                max_single_excursion_min=recomputed_eval.max_single_excursion_min,
                uncertainty_borderline_flag=True,
                compliance_status="INVESTIGATION_REQUIRED",
                disposition_decision="QUARANTINE_HOLD",
                first_breach_reading_id=recomputed_eval.first_breach_reading_id,
                evaluated_by_actor="OPERATIONS_MANAGER",
            )

        # Identify operational process anomalies purely from relational & telemetry discrepancies
        detected_anomaly = "NONE"
        evidence_list: List[str] = [report["report_id"]]

        if len(assigned_logger_ids) == 0 or report.get("logger_id_evaluated") is None:
            detected_anomaly = "LOGGER_NOT_ASSIGNED"
            evidence_list.append("NO_SHIPMENT_LOGGER_ASSIGNMENT")
        elif calibration_invalid:
            detected_anomaly = "LOGGER_CALIBRATION_EXPIRED"
            evidence_list.extend(calibration_evidence)
        elif (
            shipment["assigned_policy_id"] != true_pol.policy_id
            or report["policy_id_evaluated"] != true_pol.policy_id
        ):
            detected_anomaly = "PRODUCT_THRESHOLD_MISMATCH"
            evidence_list.append(f"ASSIGNED:{shipment['assigned_policy_id']}!=CANONICAL:{true_pol.policy_id}")
        elif recomputed_eval.telemetry_gap_flagged and not report["telemetry_gap_flagged"]:
            detected_anomaly = "MISSING_DATA_SILENTLY_PASSED"
            evidence_list.append(f"MISSING_READINGS:{recomputed_eval.missing_reading_count}")
        elif recomputed_eval.custody_gap_flagged and not report["custody_gap_flagged"]:
            detected_anomaly = "CUSTODY_GAP_NOT_FLAGGED"
            evidence_list.extend(flagged_custody_ids)
        elif report["total_excursion_duration_min"] != recomputed_eval.total_excursion_duration_min:
            detected_anomaly = "CROSS_SHIPMENT_EXCURSION_CALC"
            evidence_list.append(
                f"REPORTED_EXC_MIN:{report['total_excursion_duration_min']}!=TRUE_EXC_MIN:{recomputed_eval.total_excursion_duration_min}"
            )

        if recomputed_eval.first_breach_reading_id:
            evidence_list.append(recomputed_eval.first_breach_reading_id)

        requires_override = (
            detected_anomaly != "NONE"
            or report["compliance_status"] != recomputed_eval.compliance_status
            or report["disposition_decision"] != recomputed_eval.disposition_decision
        )

        if detected_anomaly != "NONE":
            summary = (
                f"Operational QA Audit detected '{detected_anomaly}': "
                f"ExcursionReport ({report['compliance_status']} / {report['disposition_decision']}) "
                f"recomputed to ({recomputed_eval.compliance_status} / {recomputed_eval.disposition_decision})."
            )
        else:
            summary = (
                f"Operational QA Audit verified report {report['report_id']}: "
                f"{recomputed_eval.compliance_status} ({recomputed_eval.disposition_decision})."
            )

        return OperationalAuditResult(
            shipment_id=sid,
            report_id=report["report_id"],
            canonical_policy_id=true_pol.policy_id,
            assigned_policy_id=shipment["assigned_policy_id"],
            detected_anomaly_type=detected_anomaly,
            has_operational_defect=detected_anomaly != "NONE",
            recomputed_eval=recomputed_eval,
            reported_compliance_status=report["compliance_status"],
            reported_disposition_decision=report["disposition_decision"],
            audited_compliance_status=recomputed_eval.compliance_status,
            audited_disposition_decision=recomputed_eval.disposition_decision,
            requires_corrective_override=requires_override,
            evidence_ids=";".join(evidence_list),
            audit_summary=summary,
        )

    @staticmethod
    def audit_dataset(datasets: Dict[str, List[Dict[str, Any]]]) -> Dict[str, OperationalAuditResult]:
        """
        Run `audit_shipment_record` across all shipments in `datasets` without touching `GroundTruth`.
        Works even when `GroundTruth` key is completely absent from `datasets`.
        """
        policies: List[PolicyThresholds] = [
            PolicyThresholds(
                policy_id=p["policy_id"],
                product_id=p["product_id"],
                min_temp_c=float(p["min_temp_c"]),
                max_temp_c=float(p["max_temp_c"]),
                critical_low_temp_c=float(p["critical_low_temp_c"]),
                critical_high_temp_c=float(p["critical_high_temp_c"]),
                max_single_excursion_min=int(p["max_single_excursion_min"]),
                max_cumulative_excursion_min=int(p["max_cumulative_excursion_min"]),
                max_mkt_c=float(p["max_mkt_c"]),
                max_telemetry_gap_min=int(p["max_telemetry_gap_min"]),
                max_custody_gap_min=int(p.get("max_custody_gap_min", CUSTODY_GAP_THRESHOLD_MIN)),
                effective_from=p.get("effective_from", "2026-01-01T00:00:00Z"),
                effective_to=p.get("effective_to") or None,
                version=int(p.get("version", 1)),
            )
            for p in datasets["ThresholdPolicy"]
        ]

        logger_by_id: Dict[str, Dict[str, Any]] = {
            lg["logger_id"]: lg for lg in datasets.get("Logger", [])
        }
        assigned_loggers_by_shp: Dict[str, List[str]] = {}
        assigned_rows_by_shp: Dict[str, List[Dict[str, Any]]] = {}
        for sl in datasets["ShipmentLogger"]:
            assigned_loggers_by_shp.setdefault(sl["shipment_id"], []).append(sl["logger_id"])
            assigned_rows_by_shp.setdefault(sl["shipment_id"], []).append(sl)

        readings_by_shp: Dict[str, List[ReadingPoint]] = {}
        for r in datasets["TemperatureReading"]:
            sid = r.get("shipment_id")
            if not sid:
                continue
            readings_by_shp.setdefault(sid, []).append(
                ReadingPoint(
                    reading_id=r["reading_id"],
                    logger_id=r["logger_id"],
                    shipment_id=sid,
                    sequence_no=int(r["sequence_no"]),
                    recorded_at_utc=r["recorded_at_utc"],
                    temperature_c=float(r["temperature_c"]),
                    calibration_uncertainty_c=float(r["calibration_uncertainty_c"]),
                    elapsed_since_prev_min=int(r["elapsed_since_prev_min"]),
                )
            )

        custody_by_shp: Dict[str, List[Dict[str, Any]]] = {}
        for ce in datasets["CustodyEvent"]:
            custody_by_shp.setdefault(ce["shipment_id"], []).append(ce)

        report_by_shp = {er["shipment_id"]: er for er in datasets["ExcursionReport"]}

        results: Dict[str, OperationalAuditResult] = {}
        for shp in datasets["Shipment"]:
            sid = shp["shipment_id"]
            lids = assigned_loggers_by_shp.get(sid, [])
            lrows = [logger_by_id[lid] for lid in lids if lid in logger_by_id]
            results[sid] = ComplianceEngine.audit_shipment_record(
                shipment=shp,
                report=report_by_shp[sid],
                policies=policies,
                assigned_logger_ids=lids,
                readings=readings_by_shp.get(sid, []),
                custody_events=custody_by_shp.get(sid, []),
                assigned_logger_rows=lrows,
                assigned_assignment_rows=assigned_rows_by_shp.get(sid, []),
            )
        return results
