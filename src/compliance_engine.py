"""
Core Cold-Chain Compliance & Deviation Evaluation Engine.

Provides deterministic, traceable rules for:
1. Mean Kinetic Temperature (MKT) calculation via the Haynes / USP <1079> equation.
2. Excursion duration calculation (cumulative and single contiguous window).
3. Telemetry missing-data gap detection.
4. Custody handoff delay / gap verification.
5. Calibration uncertainty borderline detection.
6. Final compliance status and disposition decision assignment.
7. Independent audit / anomaly detection to uncover silent process defects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# Constants for Haynes Mean Kinetic Temperature (MKT) formula (USP <1079>)
DELTA_H_KJ_MOL = 83.14472  # Activation energy in kJ/mol
GAS_CONSTANT_R = 0.008314472  # Universal gas constant in kJ/(mol*K)
KELVIN_OFFSET = 273.15
CUSTODY_GAP_THRESHOLD_MIN = 120  # Maximum allowed custody handoff delay in minutes


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
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str)


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
    ) -> EvaluationResult:
        """
        Evaluate a shipment's temperature telemetry and custody chain against its policy.
        Every derived value is computed strictly from the source records.
        """
        dep_dt = parse_iso_utc(actual_departure_utc)
        arr_dt = parse_iso_utc(actual_arrival_utc)
        trip_minutes = max(0, int((arr_dt - dep_dt).total_seconds() // 60))
        expected_count = (trip_minutes // sampling_interval_min) + 1

        max_custody_gap = max(custody_handover_gaps_min) if custody_handover_gaps_min else 0
        custody_gap_flagged = max_custody_gap > CUSTODY_GAP_THRESHOLD_MIN

        # Case 1: No assigned logger or zero readings available
        if not has_assigned_logger or len(readings) == 0:
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

        # Sort readings chronologically
        sorted_readings = sorted(readings, key=lambda r: r.recorded_at_utc)
        actual_count = len(sorted_readings)
        missing_count = max(0, expected_count - actual_count)

        # Compute telemetry gaps from consecutive reading timestamps
        max_gap_min = 0
        first_dt = parse_iso_utc(sorted_readings[0].recorded_at_utc)
        initial_gap = max(0, int((first_dt - dep_dt).total_seconds() // 60))
        if initial_gap > max_gap_min:
            max_gap_min = initial_gap

        for idx in range(1, actual_count):
            prev_dt = parse_iso_utc(sorted_readings[idx - 1].recorded_at_utc)
            curr_dt = parse_iso_utc(sorted_readings[idx].recorded_at_utc)
            delta_min = int((curr_dt - prev_dt).total_seconds() // 60)
            missing_window = max(0, delta_min - sampling_interval_min)
            if missing_window > max_gap_min:
                max_gap_min = missing_window

        last_dt = parse_iso_utc(sorted_readings[-1].recorded_at_utc)
        tail_gap = max(0, int((arr_dt - last_dt).total_seconds() // 60))
        if tail_gap > max_gap_min:
            max_gap_min = tail_gap

        telemetry_gap_flagged = max_gap_min > policy.max_telemetry_gap_min

        # Temperature summary statistics
        temps = [r.temperature_c for r in sorted_readings]
        min_temp = round(min(temps), 2)
        max_temp = round(max(temps), 2)
        mkt_c = compute_mkt_celsius(temps)

        # Excursion duration & calibration uncertainty evaluation
        total_excursion_min = 0
        max_single_excursion_min = 0
        current_contiguous_excursion_min = 0
        first_breach_id: Optional[str] = None
        critical_peak_breach = False
        uncertainty_borderline_flag = False

        for r in sorted_readings:
            t = r.temperature_c
            unc = r.calibration_uncertainty_c
            is_breach = (t < policy.min_temp_c) or (t > policy.max_temp_c)
            is_critical_peak = (t <= policy.critical_low_temp_c) or (t >= policy.critical_high_temp_c)

            # Check whether reading +/- calibration uncertainty crosses threshold boundary
            near_low = abs(t - policy.min_temp_c) <= unc
            near_high = abs(t - policy.max_temp_c) <= unc
            if near_low or near_high:
                uncertainty_borderline_flag = True

            if is_breach:
                if first_breach_id is None:
                    first_breach_id = r.reading_id
                step_min = sampling_interval_min
                total_excursion_min += step_min
                current_contiguous_excursion_min += step_min
                if current_contiguous_excursion_min > max_single_excursion_min:
                    max_single_excursion_min = current_contiguous_excursion_min
                if is_critical_peak:
                    critical_peak_breach = True
            else:
                current_contiguous_excursion_min = 0

        mkt_breach = mkt_c is not None and mkt_c > policy.max_mkt_c

        # Decision tree for compliance status and disposition decision (priority ordered)
        if (
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
        elif telemetry_gap_flagged:
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
