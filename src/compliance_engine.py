"""
Core Cold-Chain Compliance & Deviation Evaluation Engine.

Provides deterministic, traceable rules for:
1. Mean Kinetic Temperature (MKT) calculation via the Haynes / USP <1079> equation.
2. Excursion duration calculation across regular and irregular sampling intervals.
3. Telemetry missing-data gap detection on an explicit expected time grid, plus duplicate
   timestamp / reading ID detection and out-of-window filtering.
4. Product-to-Policy compatibility and effective-date window verification.
5. Configurable custody handoff delay / gap verification.
6. Calibration uncertainty borderline and excursion-across-gap uncertainty detection.
7. Final compliance status and disposition decision assignment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Set, Tuple


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

    ratio = DELTA_H_KJ_MOL / GAS_CONSTANT_R  # 10,000 K
    # Use log-sum-exp trick to avoid floating point underflow/overflow at extreme temperatures
    exponents = [-ratio / (t + KELVIN_OFFSET) for t in temperatures_c]
    max_exp = max(exponents)
    sum_shifted = sum(math.exp(e - max_exp) for e in exponents)
    mean_log = max_exp + math.log(sum_shifted / len(temperatures_c))
    mkt_kelvin = -ratio / mean_log
    return round(mkt_kelvin - KELVIN_OFFSET, 2)


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
        Every derived value is computed strictly from the source records, enforcing:
        - Product-to-policy identity and effective-window compatibility (Bug 4 fix)
        - Cross-shipment / unassigned logger reading rejection (QA-09 fix)
        - Duplicate timestamp / duplicate reading_id detection & expected grid coverage (Bug 2 fix)
        - Missing-data gap detection and evidence sufficiency enforcement (Bug 1 fix)
        - Timestamp-based excursion duration & gap-uncertainty flagging (Bug 3 fix)
        """
        dep_dt = parse_iso_utc(actual_departure_utc)
        arr_dt = parse_iso_utc(actual_arrival_utc)
        if arr_dt < dep_dt:
            raise ValueError("actual_arrival_utc cannot be earlier than actual_departure_utc")

        trip_minutes = max(0, int((arr_dt - dep_dt).total_seconds() // 60))
        expected_count = (trip_minutes // sampling_interval_min) + 1

        # Build the exact expected 10-minute timestamp grid from departure to arrival
        expected_grid_dts: Set[datetime] = {
            dep_dt + timedelta(minutes=i * sampling_interval_min)
            for i in range(expected_count)
        }

        # Verify custody handoff gaps against configurable policy threshold
        custody_limit_min = getattr(policy, "max_custody_gap_min", CUSTODY_GAP_THRESHOLD_MIN)
        max_custody_gap = max(custody_handover_gaps_min) if custody_handover_gaps_min else 0
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

        # Filter and inspect readings for cross-shipment contamination, outside-window timestamps,
        # and duplicate reading IDs or duplicate timestamps (Bug 2 & QA-09)
        duplicate_detected = False
        contamination_or_out_of_window_detected = False
        seen_reading_ids: Set[str] = set()
        seen_timestamps_by_logger: Set[Tuple[str, datetime]] = set()
        valid_readings_by_dt: dict[datetime, ReadingPoint] = {}

        for r in readings:
            # Check reading_id uniqueness
            if r.reading_id in seen_reading_ids:
                duplicate_detected = True
                continue
            seen_reading_ids.add(r.reading_id)

            # Check cross-shipment contamination (QA-09)
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

            # Check duplicate timestamp for the same logger (Bug 2)
            key = (r.logger_id, r_dt)
            if key in seen_timestamps_by_logger or r_dt in valid_readings_by_dt:
                duplicate_detected = True
                continue
            seen_timestamps_by_logger.add(key)

            # Reject readings outside the shipment window [dep_dt, arr_dt]
            if r_dt < dep_dt or r_dt > arr_dt:
                contamination_or_out_of_window_detected = True
                continue

            valid_readings_by_dt[r_dt] = r

        # Case 1: No assigned logger or zero valid readings available
        if not has_assigned_logger or len(valid_readings_by_dt) == 0:
            return EvaluationResult(
                expected_reading_count=expected_count,
                actual_reading_count=0,
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

        # Calculate missing readings strictly from the expected time grid, never raw row count (Bug 2 fix)
        covered_grid_points = sum(1 for g_dt in expected_grid_dts if g_dt in valid_readings_by_dt)
        missing_count = max(0, expected_count - covered_grid_points)

        # Compute telemetry gaps from consecutive reading timestamps + initial/tail gaps
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

        # Bug 1 & Bug 2 Fix:
        # 1. Missing-data gap reaching OR exceeding (>=) policy.max_telemetry_gap_min is flagged.
        # 2. Any missing grid points when duplicate timestamps occurred or missing departure/arrival endpoints
        #    also flags insufficient telemetry evidence.
        telemetry_gap_flagged = (
            max_gap_min >= policy.max_telemetry_gap_min
            or duplicate_detected
            or (missing_count > 0 and max_gap_min > 0)
        )

        # Temperature summary statistics
        temps = [r.temperature_c for r in sorted_readings]
        min_temp = round(min(temps), 2)
        max_temp = round(max(temps), 2)
        mkt_c = compute_mkt_celsius(temps)

        # Excursion duration & calibration uncertainty evaluation (Bug 3 Fix)
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
            # Inclusive safe range: [min_temp_c, max_temp_c]. Strictly outside is a breach (QA-02).
            is_breach = (t < policy.min_temp_c) or (t > policy.max_temp_c)
            is_critical_peak = (t <= policy.critical_low_temp_c) or (t >= policy.critical_high_temp_c)

            # Calibration uncertainty borderline check:
            # Only flag borderline uncertainty when the reading is strictly inside the threshold
            # boundary (not exactly equal to target, and within (0, unc] of min_temp_c or max_temp_c)
            dist_low = abs(t - policy.min_temp_c)
            dist_high = abs(t - policy.max_temp_c)
            if (0.0 < dist_low <= unc) or (0.0 < dist_high <= unc):
                uncertainty_borderline_flag = True

            if is_breach:
                if first_breach_id is None:
                    first_breach_id = r.reading_id

                # Bug 3 Fix: Compute actual elapsed interval rather than assuming fixed 10 minutes
                if idx > 0:
                    actual_delta_min = int((sorted_dts[idx] - sorted_dts[idx - 1]).total_seconds() // 60)
                else:
                    actual_delta_min = sampling_interval_min

                prev_was_breach = (
                    idx > 0
                    and (
                        sorted_readings[idx - 1].temperature_c < policy.min_temp_c
                        or sorted_readings[idx - 1].temperature_c > policy.max_temp_c
                    )
                )

                if actual_delta_min > sampling_interval_min:
                    # There is a missing-data gap immediately preceding this breach reading.
                    # The true excursion duration across the gap is uncertain; conservatively account
                    # for the full elapsed span if the previous reading was also in breach, and flag uncertainty!
                    excursion_across_gap_uncertainty = True
                    uncertainty_borderline_flag = True
                    step_min = actual_delta_min if prev_was_breach else sampling_interval_min
                else:
                    step_min = sampling_interval_min

                total_excursion_min += step_min
                current_contiguous_excursion_min += step_min
                if current_contiguous_excursion_min > max_single_excursion_min:
                    max_single_excursion_min = current_contiguous_excursion_min
                if is_critical_peak:
                    critical_peak_breach = True
            else:
                # If previous reading was a breach and current reading is after a gap, true duration is uncertain
                if idx > 0:
                    actual_delta_min = int((sorted_dts[idx] - sorted_dts[idx - 1]).total_seconds() // 60)
                    prev_t = sorted_readings[idx - 1].temperature_c
                    if actual_delta_min > sampling_interval_min and (
                        prev_t < policy.min_temp_c or prev_t > policy.max_temp_c
                    ):
                        excursion_across_gap_uncertainty = True
                        uncertainty_borderline_flag = True
                current_contiguous_excursion_min = 0

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
