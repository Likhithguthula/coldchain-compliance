"""
Deterministic Synthetic Data Generator for Cold-Chain Shipment Compliance & Deviation Management.

Implements all 10 granular synthetic data generation steps:
- Step 1: Enforces Data Dictionary schema across 10 core entities + 2 bridge tables + 1 state history table.
- Step 2: Master tables first (Product, ThresholdPolicy, Packaging, Lot, Logger), then transactions/events.
- Step 3: Strict deterministic nomenclature (LOT-2026-000001, SHP-2026-000001, LOG-<shipment-id>-01,
          TEMP-<logger-id>-<timestamp>, EXCR-2026-000001).
- Step 4: Full scale (5,000 shipments, 1,000 loggers, 10-minute readings) & starter scale parameterized,
          with fixed default seed (20261022).
- Step 5: Valid baseline records generated first, followed by 5 controlled, labelled negative edge cases:
          1) LOGGER_NOT_ASSIGNED
          2) PRODUCT_THRESHOLD_MISMATCH
          3) MISSING_DATA_SILENTLY_PASSED
          4) CUSTODY_GAP_NOT_FLAGGED
          5) CROSS_SHIPMENT_EXCURSION_CALC
- Step 6: All derived metrics (MKT, excursion minutes, expected/missing counts, values, lifecycle states)
          computed directly from source records.
- Step 7: Separate ground_truth.csv / ground_truth.json without leaking into input tables.
- Step 8 & 9: Exports CSV, JSON, SQLite DB / SQL seed dump, source manifest, and runs full validation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.compliance_engine import (
    CUSTODY_GAP_THRESHOLD_MIN,
    ComplianceEngine,
    PolicyThresholds,
    ReadingPoint,
    compute_mkt_celsius,
    parse_iso_utc,
)

DEFAULT_SEED = 20261022

SCALE_PRESETS = {
    "full": {
        "num_shipments": 5000,
        "num_loggers": 1000,
        "num_lots": 400,
    },
    "starter": {
        "num_shipments": 250,
        "num_loggers": 100,
        "num_lots": 50,
    },
}

PRODUCTS_CATALOG = [
    {
        "sku": "BIO-MAB-001",
        "name": "OncoShield Monoclonal Antibody 100mg/mL",
        "category": "MONOCLONAL_ANTIBODY",
        "storage_regime": "REFRIGERATED",
        "target_temp_c": 5.0,
        "min_temp_c": 2.0,
        "max_temp_c": 8.0,
        "critical_low_temp_c": 0.0,
        "critical_high_temp_c": 12.0,
        "max_single_excursion_min": 40,
        "max_cumulative_excursion_min": 90,
        "max_mkt_c": 8.3,
        "max_telemetry_gap_min": 30,
        "unit_value_usd": 420.0,
        "humidity_sensitive": True,
    },
    {
        "sku": "VAC-MRNA-002",
        "name": "PneumoGuard mRNA Lipid Nanoparticle Vaccine",
        "category": "MRNA_THERAPY",
        "storage_regime": "ULT_DEEP_FREEZE",
        "target_temp_c": -70.0,
        "min_temp_c": -80.0,
        "max_temp_c": -60.0,
        "critical_low_temp_c": -90.0,
        "critical_high_temp_c": -50.0,
        "max_single_excursion_min": 20,
        "max_cumulative_excursion_min": 40,
        "max_mkt_c": -58.5,
        "max_telemetry_gap_min": 20,
        "unit_value_usd": 185.0,
        "humidity_sensitive": False,
    },
    {
        "sku": "VAC-VIR-003",
        "name": " ZosterVax Live Attenuated Viral Bulk",
        "category": "VACCINE",
        "storage_regime": "FROZEN",
        "target_temp_c": -20.0,
        "min_temp_c": -25.0,
        "max_temp_c": -15.0,
        "critical_low_temp_c": -32.0,
        "critical_high_temp_c": -10.0,
        "max_single_excursion_min": 30,
        "max_cumulative_excursion_min": 60,
        "max_mkt_c": -14.2,
        "max_telemetry_gap_min": 30,
        "unit_value_usd": 95.0,
        "humidity_sensitive": False,
    },
    {
        "sku": "INS-PEP-004",
        "name": "GlucoReg Recombinant Insulin Analog Cartridges",
        "category": "INSULIN_PEPTIDE",
        "storage_regime": "REFRIGERATED",
        "target_temp_c": 4.5,
        "min_temp_c": 2.0,
        "max_temp_c": 8.0,
        "critical_low_temp_c": 0.5,
        "critical_high_temp_c": 14.0,
        "max_single_excursion_min": 50,
        "max_cumulative_excursion_min": 120,
        "max_mkt_c": 8.5,
        "max_telemetry_gap_min": 30,
        "unit_value_usd": 65.0,
        "humidity_sensitive": True,
    },
    {
        "sku": "PLS-ALB-005",
        "name": "HemaPure Human Albumin 20% Infusion Solution",
        "category": "BLOOD_PLASMA",
        "storage_regime": "CONTROLLED_ROOM_TEMP",
        "target_temp_c": 20.0,
        "min_temp_c": 15.0,
        "max_temp_c": 25.0,
        "critical_low_temp_c": 8.0,
        "critical_high_temp_c": 30.0,
        "max_single_excursion_min": 90,
        "max_cumulative_excursion_min": 180,
        "max_mkt_c": 25.8,
        "max_telemetry_gap_min": 40,
        "unit_value_usd": 110.0,
        "humidity_sensitive": False,
    },
    {
        "sku": "BIO-CAR-006",
        "name": "CellVantage Autologous Cryo-Cell Vector",
        "category": "BIOLOGIC",
        "storage_regime": "ULT_DEEP_FREEZE",
        "target_temp_c": -72.0,
        "min_temp_c": -80.0,
        "max_temp_c": -60.0,
        "critical_low_temp_c": -88.0,
        "critical_high_temp_c": -52.0,
        "max_single_excursion_min": 20,
        "max_cumulative_excursion_min": 30,
        "max_mkt_c": -59.0,
        "max_telemetry_gap_min": 20,
        "unit_value_usd": 1250.0,
        "humidity_sensitive": False,
    },
    {
        "sku": "BIO-ENZ-007",
        "name": "LysoZyme-ERT Enzyme Replacement Therapy",
        "category": "BIOLOGIC",
        "storage_regime": "REFRIGERATED",
        "target_temp_c": 5.0,
        "min_temp_c": 2.0,
        "max_temp_c": 8.0,
        "critical_low_temp_c": -0.5,
        "critical_high_temp_c": 11.5,
        "max_single_excursion_min": 30,
        "max_cumulative_excursion_min": 70,
        "max_mkt_c": 8.2,
        "max_telemetry_gap_min": 30,
        "unit_value_usd": 780.0,
        "humidity_sensitive": True,
    },
    {
        "sku": "PLS-IGG-008",
        "name": "ImmunoGlobulin IVIG 10% Sterile Vials",
        "category": "BLOOD_PLASMA",
        "storage_regime": "REFRIGERATED",
        "target_temp_c": 5.5,
        "min_temp_c": 2.0,
        "max_temp_c": 8.0,
        "critical_low_temp_c": 0.0,
        "critical_high_temp_c": 13.0,
        "max_single_excursion_min": 60,
        "max_cumulative_excursion_min": 120,
        "max_mkt_c": 8.4,
        "max_telemetry_gap_min": 30,
        "unit_value_usd": 310.0,
        "humidity_sensitive": False,
    },
    {
        "sku": "VAC-ADJ-009",
        "name": "RespiraShield Adjuvanted Subunit Vaccine",
        "category": "VACCINE",
        "storage_regime": "FROZEN",
        "target_temp_c": -20.0,
        "min_temp_c": -25.0,
        "max_temp_c": -15.0,
        "critical_low_temp_c": -30.0,
        "critical_high_temp_c": -11.0,
        "max_single_excursion_min": 40,
        "max_cumulative_excursion_min": 80,
        "max_mkt_c": -14.5,
        "max_telemetry_gap_min": 30,
        "unit_value_usd": 140.0,
        "humidity_sensitive": True,
    },
    {
        "sku": "MAB-IMM-010",
        "name": "AutoImmuneCheck Anti-TNF Subcutaneous Syringe",
        "category": "MONOCLONAL_ANTIBODY",
        "storage_regime": "CONTROLLED_ROOM_TEMP",
        "target_temp_c": 19.5,
        "min_temp_c": 15.0,
        "max_temp_c": 25.0,
        "critical_low_temp_c": 10.0,
        "critical_high_temp_c": 29.0,
        "max_single_excursion_min": 60,
        "max_cumulative_excursion_min": 150,
        "max_mkt_c": 25.5,
        "max_telemetry_gap_min": 40,
        "unit_value_usd": 520.0,
        "humidity_sensitive": True,
    },
]

PACKAGING_CATALOG = [
    {
        "model_name": "Envirotainer RKN e1 Active Compressor ULD",
        "packaging_type": "ACTIVE_ULD_COMPRESSOR",
        "target_regime": "REFRIGERATED",
        "validated_hold_hours": 120,
        "insulation_r_value": 32.5,
        "payload_capacity_liters": 1100.0,
        "reusable": True,
    },
    {
        "model_name": "CSafe RAP Active Thermal Pallet Container",
        "packaging_type": "ACTIVE_ULD_COMPRESSOR",
        "target_regime": "REFRIGERATED",
        "validated_hold_hours": 144,
        "insulation_r_value": 36.0,
        "payload_capacity_liters": 1450.0,
        "reusable": True,
    },
    {
        "model_name": "CryoVault Dry-Ice Sublimation Shipper 96h",
        "packaging_type": "DRY_ICE_SHIPPER",
        "target_regime": "ULT_DEEP_FREEZE",
        "validated_hold_hours": 96,
        "insulation_r_value": 42.0,
        "payload_capacity_liters": 65.0,
        "reusable": True,
    },
    {
        "model_name": "CryoBox Pelton-VIP UltraDeep Dry Ice Parcel",
        "packaging_type": "DRY_ICE_SHIPPER",
        "target_regime": "ULT_DEEP_FREEZE",
        "validated_hold_hours": 120,
        "insulation_r_value": 44.5,
        "payload_capacity_liters": 40.0,
        "reusable": False,
    },
    {
        "model_name": "Va-Q-tec va-Q-tainer PCM Phase-Change -20C",
        "packaging_type": "PASSIVE_PCM_VIP",
        "target_regime": "FROZEN",
        "validated_hold_hours": 108,
        "insulation_r_value": 38.0,
        "payload_capacity_liters": 450.0,
        "reusable": True,
    },
    {
        "model_name": "Softbox Silverpod PCM Frozen Pallet Shipper",
        "packaging_type": "PASSIVE_PCM_VIP",
        "target_regime": "FROZEN",
        "validated_hold_hours": 96,
        "insulation_r_value": 34.0,
        "payload_capacity_liters": 320.0,
        "reusable": True,
    },
    {
        "model_name": "Sonoco ThermoSafe Greenbox 2-8C VIP Parcel",
        "packaging_type": "PASSIVE_PCM_VIP",
        "target_regime": "REFRIGERATED",
        "validated_hold_hours": 72,
        "insulation_r_value": 28.5,
        "payload_capacity_liters": 50.0,
        "reusable": True,
    },
    {
        "model_name": "Peli BioThermal Crēdo Cube CRT 15-25C",
        "packaging_type": "PASSIVE_PCM_VIP",
        "target_regime": "CONTROLLED_ROOM_TEMP",
        "validated_hold_hours": 96,
        "insulation_r_value": 26.0,
        "payload_capacity_liters": 85.0,
        "reusable": True,
    },
    {
        "model_name": "ColdChain GelPack Insulated EuroBox CRT",
        "packaging_type": "GEL_PACK_INSULATED",
        "target_regime": "CONTROLLED_ROOM_TEMP",
        "validated_hold_hours": 48,
        "insulation_r_value": 14.5,
        "payload_capacity_liters": 35.0,
        "reusable": False,
    },
    {
        "model_name": "IsoTherm GelPack Chilled Courier Box 2-8C",
        "packaging_type": "GEL_PACK_INSULATED",
        "target_regime": "REFRIGERATED",
        "validated_hold_hours": 48,
        "insulation_r_value": 16.0,
        "payload_capacity_liters": 25.0,
        "reusable": False,
    },
]

ROUTES_CATALOG = [
    {
        "origin": "CHBSL-BASEL-MFG",
        "hub": "DEJFK-FRANKFURT-AIR",
        "dest": "USNYC-JFK-COLDHUB",
        "mode": "AIR_FREIGHT",
        "distance_km": 6320.0,
        "ambient_origin_c": 14.0,
        "ambient_hub_c": 26.5,
        "ambient_dest_c": 19.0,
    },
    {
        "origin": "USBOS-CAMBRIDGE-BIO",
        "hub": "USMEM-MEMPHIS-SUPERHUB",
        "dest": "USLAX-WESTCOAST-DIST",
        "mode": "AIR_FREIGHT",
        "distance_km": 4180.0,
        "ambient_origin_c": 12.0,
        "ambient_hub_c": 31.0,
        "ambient_dest_c": 24.0,
    },
    {
        "origin": "BEANR-ANTWERP-PHARMA",
        "hub": "AEDXB-DUBAI-WORLD-CENTRAL",
        "dest": "SGSIN-CHANGI-BIOHUB",
        "mode": "AIR_FREIGHT",
        "distance_km": 10650.0,
        "ambient_origin_c": 11.5,
        "ambient_hub_c": 38.5,
        "ambient_dest_c": 30.5,
    },
    {
        "origin": "DEHAM-HAMBURG-LABS",
        "hub": "NLRTM-ROTTERDAM-CROSSDOCK",
        "dest": "GBLON-HEATHROW-MED",
        "mode": "ROAD_REEFER",
        "distance_km": 840.0,
        "ambient_origin_c": 10.0,
        "ambient_hub_c": 13.5,
        "ambient_dest_c": 12.5,
    },
    {
        "origin": "INHYD-HYDERABAD-VAX",
        "hub": "DOH-HAMAD-CARGO-HUB",
        "dest": "BRGRU-SAOPAULO-DIST",
        "mode": "MULTIMODAL_AIR_ROAD",
        "distance_km": 13890.0,
        "ambient_origin_c": 32.0,
        "ambient_hub_c": 39.0,
        "ambient_dest_c": 27.0,
    },
    {
        "origin": "JPOSA-OSAKA-CELL",
        "hub": "KRINC-INCHEON-COOLPORT",
        "dest": "AUSYD-SYDNEY-CLINICAL",
        "mode": "AIR_FREIGHT",
        "distance_km": 8350.0,
        "ambient_origin_c": 16.0,
        "ambient_hub_c": 18.0,
        "ambient_dest_c": 25.0,
    },
    {
        "origin": "IEORK-CORK-BIOPHARMA",
        "hub": "FRCDG-PARIS-CARGO",
        "dest": "ITMIL-MALPENSA-MED",
        "mode": "MULTIMODAL_AIR_ROAD",
        "distance_km": 1720.0,
        "ambient_origin_c": 11.0,
        "ambient_hub_c": 21.0,
        "ambient_dest_c": 23.5,
    },
]

SUPPLIERS = [
    "SUP-HELIX-BIOPHARMA-AG",
    "SUP-NOVAVAX-GLOBAL-LTD",
    "SUP-CELLECTA-THERAPEUTICS",
    "SUP-AETHER-BIOLOGICS-INC",
    "SUP-GENOME-CRYO-LABS",
]

PLANNERS = [
    "PLN-E.VANCE",
    "PLN-M.KOWALSKI",
    "PLN-A.SUBRAMANIAN",
    "PLN-S.LINDQVIST",
    "PLN-D.CHEN",
]

CARRIERS = [
    "CAR-LUFTHANSA-CARGO-COOL",
    "CAR-DHL-GLOBAL-THERMONET",
    "CAR-FEDEX-CUSTOM-CRITICAL",
    "CAR-KUEHNE-NAGEL-MEDLOG",
    "CAR-MAERSK-COLD-CHAIN",
]

LOGGER_MODELS = [
    ("Sensitech-TempTale-Geo-Ultra", "v4.2.1"),
    ("ELPRO-LIBERO-GE", "v3.8.0"),
    ("Berlinger-Q-tag-CLm", "v2.5.4"),
    ("Testo-184-T4", "v5.0.2"),
]

MFG_SITES = [
    "BASEL-CH-01",
    "CAMBRIDGE-US-02",
    "ANTWERP-BE-01",
    "HYDERABAD-IN-03",
    "OSAKA-JP-01",
    "CORK-IE-02",
]


def format_iso_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_compact_ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def compute_signature_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class SyntheticDataGenerator:
    """Deterministic generator for the Cold-Chain Compliance & Deviation dataset."""

    def __init__(self, seed: int = DEFAULT_SEED, scale: str = "full"):
        if scale not in SCALE_PRESETS:
            raise ValueError(f"Unsupported scale '{scale}'. Choose from {list(SCALE_PRESETS.keys())}")
        self.seed = seed
        self.scale = scale
        self.config = SCALE_PRESETS[scale]
        self.rng = random.Random(seed)

        # In-memory tables
        self.products: List[Dict[str, Any]] = []
        self.threshold_policies: List[Dict[str, Any]] = []
        self.packaging: List[Dict[str, Any]] = []
        self.lots: List[Dict[str, Any]] = []
        self.loggers: List[Dict[str, Any]] = []
        self.shipments: List[Dict[str, Any]] = []
        self.shipment_lots: List[Dict[str, Any]] = []
        self.shipment_loggers: List[Dict[str, Any]] = []
        self.route_stops: List[Dict[str, Any]] = []
        self.custody_events: List[Dict[str, Any]] = []
        self.temperature_readings: List[Dict[str, Any]] = []
        self.excursion_reports: List[Dict[str, Any]] = []
        self.shipment_state_history: List[Dict[str, Any]] = []
        self.ground_truth: List[Dict[str, Any]] = []

        # Lookup maps
        self.product_by_id: Dict[str, Dict[str, Any]] = {}
        self.policy_by_id: Dict[str, PolicyThresholds] = {}
        self.canonical_policy_by_product: Dict[str, PolicyThresholds] = {}
        self.packaging_by_regime: Dict[str, List[Dict[str, Any]]] = {}
        self.lots_by_product: Dict[str, List[Dict[str, Any]]] = {}

    def generate_all(self) -> Dict[str, List[Dict[str, Any]]]:
        """Run the full 10-step deterministic generation pipeline."""
        self._generate_master_products_and_policies()
        self._generate_master_packaging()
        self._generate_master_lots()
        self._generate_master_loggers()
        self._generate_shipments_and_events()

        return {
            "Product": self.products,
            "ThresholdPolicy": self.threshold_policies,
            "Packaging": self.packaging,
            "Lot": self.lots,
            "Logger": self.loggers,
            "Shipment": self.shipments,
            "ShipmentLot": self.shipment_lots,
            "ShipmentLogger": self.shipment_loggers,
            "RouteStop": self.route_stops,
            "CustodyEvent": self.custody_events,
            "TemperatureReading": self.temperature_readings,
            "ExcursionReport": self.excursion_reports,
            "ShipmentStateHistory": self.shipment_state_history,
            "GroundTruth": self.ground_truth,
        }

    def _generate_master_products_and_policies(self) -> None:
        for idx, item in enumerate(PRODUCTS_CATALOG, start=1):
            prod_id = f"PROD-2026-{idx:04d}"
            pol_id = f"POL-2026-{idx:04d}"

            product_row = {
                "product_id": prod_id,
                "sku": item["sku"].strip(),
                "name": item["name"].strip(),
                "category": item["category"],
                "storage_regime": item["storage_regime"],
                "target_temp_c": item["target_temp_c"],
                "unit_value_usd": item["unit_value_usd"],
                "humidity_sensitive": item["humidity_sensitive"],
                "lifecycle_state": "ACTIVE",
                "effective_from": "2026-01-01T00:00:00Z",
                "effective_to": None,
            }
            self.products.append(product_row)
            self.product_by_id[prod_id] = product_row

            policy_row = {
                "policy_id": pol_id,
                "product_id": prod_id,
                "version": 1,
                "min_temp_c": item["min_temp_c"],
                "max_temp_c": item["max_temp_c"],
                "critical_low_temp_c": item["critical_low_temp_c"],
                "critical_high_temp_c": item["critical_high_temp_c"],
                "max_single_excursion_min": item["max_single_excursion_min"],
                "max_cumulative_excursion_min": item["max_cumulative_excursion_min"],
                "max_mkt_c": item["max_mkt_c"],
                "max_telemetry_gap_min": item["max_telemetry_gap_min"],
                "effective_from": "2026-01-01T00:00:00Z",
                "effective_to": None,
                "lifecycle_state": "ACTIVE",
            }
            self.threshold_policies.append(policy_row)

            pol_obj = PolicyThresholds(
                policy_id=pol_id,
                product_id=prod_id,
                min_temp_c=item["min_temp_c"],
                max_temp_c=item["max_temp_c"],
                critical_low_temp_c=item["critical_low_temp_c"],
                critical_high_temp_c=item["critical_high_temp_c"],
                max_single_excursion_min=item["max_single_excursion_min"],
                max_cumulative_excursion_min=item["max_cumulative_excursion_min"],
                max_mkt_c=item["max_mkt_c"],
                max_telemetry_gap_min=item["max_telemetry_gap_min"],
            )
            self.policy_by_id[pol_id] = pol_obj
            self.canonical_policy_by_product[prod_id] = pol_obj

    def _generate_master_packaging(self) -> None:
        for idx, pkg in enumerate(PACKAGING_CATALOG, start=1):
            pkg_id = f"PKG-2026-{idx:04d}"
            row = {
                "packaging_id": pkg_id,
                "model_name": pkg["model_name"],
                "packaging_type": pkg["packaging_type"],
                "target_regime": pkg["target_regime"],
                "validated_hold_hours": pkg["validated_hold_hours"],
                "insulation_r_value": pkg["insulation_r_value"],
                "payload_capacity_liters": pkg["payload_capacity_liters"],
                "reusable": pkg["reusable"],
                "lifecycle_state": "QUALIFIED",
            }
            self.packaging.append(row)
            self.packaging_by_regime.setdefault(pkg["target_regime"], []).append(row)

    def _generate_master_lots(self) -> None:
        num_lots = self.config["num_lots"]
        base_mfg_date = datetime(2025, 10, 1, tzinfo=timezone.utc).date()
        for idx in range(1, num_lots + 1):
            lot_id = f"LOT-2026-{idx:06d}"
            product = self.products[(idx - 1) % len(self.products)]
            prod_id = product["product_id"]
            pol = self.canonical_policy_by_product[prod_id]

            mfg_offset = self.rng.randint(0, 90)
            mfg_date = base_mfg_date + timedelta(days=mfg_offset)
            shelf_days = self.rng.choice([365, 540, 730])
            exp_date = mfg_date + timedelta(days=shelf_days)

            lot_row = {
                "lot_id": lot_id,
                "product_id": prod_id,
                "manufacturing_site": self.rng.choice(MFG_SITES),
                "manufactured_date": mfg_date.isoformat(),
                "expiry_date": exp_date.isoformat(),
                "initial_quantity_units": self.rng.randint(2500, 25000),
                "remaining_stability_budget_min": pol.max_cumulative_excursion_min,
                "lifecycle_state": "RELEASED",
            }
            self.lots.append(lot_row)
            self.lots_by_product.setdefault(prod_id, []).append(lot_row)

    def _generate_master_loggers(self) -> None:
        """
        Generate 1,000 physical loggers (or scaled count).
        Per ID nomenclature convention: LOG-<shipment-id>-01 where <shipment-id> is the
        first canonical shipment ID (SHP-2026-000001 .. SHP-2026-001000) to which the logger
        was commissioned, and reused across subsequent non-overlapping waves of shipments.
        """
        num_loggers = self.config["num_loggers"]
        for idx in range(1, num_loggers + 1):
            home_shipment_id = f"SHP-2026-{idx:06d}"
            logger_id = f"LOG-{home_shipment_id}-01"
            serial_no = f"SN-YF-{20260000 + idx:08d}"
            model_name, fw_ver = LOGGER_MODELS[(idx - 1) % len(LOGGER_MODELS)]
            cal_unc = round(self.rng.choice([0.15, 0.20, 0.25, 0.30, 0.40, 0.50]), 2)

            cal_date = datetime(2025, 11, 1, tzinfo=timezone.utc).date() + timedelta(days=idx % 60)
            cal_exp = cal_date + timedelta(days=365)

            logger_row = {
                "logger_id": logger_id,
                "serial_number": serial_no,
                "manufacturer_model": model_name,
                "sampling_interval_min": 10,
                "calibration_uncertainty_c": cal_unc,
                "calibration_date": cal_date.isoformat(),
                "calibration_expiry_date": cal_exp.isoformat(),
                "battery_pct": self.rng.randint(35, 100),
                "firmware_version": fw_ver,
                "lifecycle_state": "ACTIVE_CALIBRATED",
            }
            self.loggers.append(logger_row)

    def _assign_scenario(self, shipment_idx: int, total_shipments: int) -> Tuple[str, str]:
        """
        Deterministically assign scenario category and edge_case_type.
        Ensures balanced normal trips, excursions, calibration uncertainty borderline cases,
        logger gaps, route dwell events, and all 5 required negative edge cases.
        """
        # Reserve specific slices of shipments for the 5 negative edge cases (approx 5% total, 1% each)
        # Using modular arithmetic + deterministic bands so both starter (250) and full (5000) have all 5 edge cases!
        mod100 = (shipment_idx - 1) % 100
        if mod100 == 17:
            return ("EDGE_LOGGER_NOT_ASSIGNED", "LOGGER_NOT_ASSIGNED")
        if mod100 == 33:
            return ("EDGE_PRODUCT_THRESHOLD_MISMATCH", "PRODUCT_THRESHOLD_MISMATCH")
        if mod100 == 51:
            return ("EDGE_MISSING_DATA_SILENTLY_PASSED", "MISSING_DATA_SILENTLY_PASSED")
        if mod100 == 69:
            return ("EDGE_CUSTODY_GAP_NOT_FLAGGED", "CUSTODY_GAP_NOT_FLAGGED")
        if mod100 == 87:
            return ("EDGE_CROSS_SHIPMENT_EXCURSION_CALC", "CROSS_SHIPMENT_EXCURSION_CALC")

        # Standard operational scenarios for the remaining 95% of shipments:
        if mod100 in (5, 15, 25, 45, 65, 85):
            return ("MINOR_EXCURSION", "NONE")
        if mod100 in (8, 28, 48, 78):
            return ("CRITICAL_EXCURSION", "NONE")
        if mod100 in (12, 42, 72, 92):
            return ("ROUTE_DWELL_EXCURSION", "NONE")
        if mod100 in (19, 59, 89):
            return ("CALIBRATION_BORDERLINE", "NONE")
        if mod100 in (23, 63, 93):
            return ("TELEMETRY_GAP_FLAGGED", "NONE")

        return ("NORMAL_COMPLIANT", "NONE")

    def _generate_shipments_and_events(self) -> None:
        num_shipments = self.config["num_shipments"]
        num_loggers = self.config["num_loggers"]

        base_epoch = datetime(2026, 1, 2, 6, 0, 0, tzinfo=timezone.utc)

        shplot_counter = 0
        assgn_counter = 0
        cust_counter = 0
        sthist_counter = 0

        # Track previous shipment's readings per logger so CROSS_SHIPMENT_EXCURSION_CALC can be authentically simulated
        prev_shipment_excursion_min_by_logger: Dict[str, int] = {}

        for shp_idx in range(1, num_shipments + 1):
            shipment_id = f"SHP-2026-{shp_idx:06d}"
            report_id = f"EXCR-2026-{shp_idx:06d}"

            scenario_category, edge_case_type = self._assign_scenario(shp_idx, num_shipments)

            # Assign logger via non-overlapping wave scheduling:
            # Wave `w = (shp_idx - 1) // num_loggers`, logger_slot = `(shp_idx - 1) % num_loggers`
            wave_idx = (shp_idx - 1) // num_loggers
            logger_slot = (shp_idx - 1) % num_loggers
            logger_row = self.loggers[logger_slot]
            logger_id = logger_row["logger_id"]
            cal_unc = logger_row["calibration_uncertainty_c"]

            # Ensure waves are separated by 24 hours so a physical logger is never on two shipments simultaneously
            wave_start_dt = base_epoch + timedelta(hours=wave_idx * 24 + (logger_slot % 12))
            trip_duration_min = self.rng.choice([120, 150, 180, 210, 240])  # 2.0 to 4.0 hours (13 to 25 readings)

            planned_dep_dt = wave_start_dt
            planned_arr_dt = planned_dep_dt + timedelta(minutes=trip_duration_min)
            actual_dep_dt = planned_dep_dt + timedelta(minutes=self.rng.choice([0, 10, 20]))
            actual_arr_dt = actual_dep_dt + timedelta(minutes=trip_duration_min)

            # Select product & canonical threshold policy
            product_row = self.products[(shp_idx - 1) % len(self.products)]
            prod_id = product_row["product_id"]
            true_policy = self.canonical_policy_by_product[prod_id]

            # If EDGE_PRODUCT_THRESHOLD_MISMATCH: bind a wrong product's policy to the shipment!
            if edge_case_type == "PRODUCT_THRESHOLD_MISMATCH":
                # Pick a policy from a radically different temperature regime so thresholds mismatch
                wrong_policy_candidates = [
                    p for p in self.policy_by_id.values()
                    if p.product_id != prod_id and abs(p.max_temp_c - true_policy.max_temp_c) >= 12.0
                ]
                assigned_policy = wrong_policy_candidates[0]
            else:
                assigned_policy = true_policy

            # Select qualified packaging matching product storage regime
            regime_pkgs = self.packaging_by_regime[product_row["storage_regime"]]
            pkg_row = regime_pkgs[(shp_idx - 1) % len(regime_pkgs)]

            # Select route
            route_info = ROUTES_CATALOG[(shp_idx - 1) % len(ROUTES_CATALOG)]

            # Select 1 or 2 lots for this product (ShipmentLot bridge table)
            prod_lots = self.lots_by_product[prod_id]
            primary_lot = prod_lots[(shp_idx - 1) % len(prod_lots)]
            selected_lots = [primary_lot]
            if shp_idx % 5 == 0 and len(prod_lots) > 1:
                second_lot = prod_lots[shp_idx % len(prod_lots)]
                if second_lot["lot_id"] != primary_lot["lot_id"]:
                    selected_lots.append(second_lot)

            declared_value_usd = 0.0
            for lot_item in selected_lots:
                shplot_counter += 1
                qty = self.rng.randint(25, 250)
                declared_value_usd += round(qty * product_row["unit_value_usd"], 2)
                self.shipment_lots.append({
                    "shipment_lot_id": f"SHPLOT-2026-{shplot_counter:06d}",
                    "shipment_id": shipment_id,
                    "lot_id": lot_item["lot_id"],
                    "quantity_units": qty,
                })
            declared_value_usd = round(declared_value_usd, 2)

            # Bind logger in ShipmentLogger bridge table (unless EDGE_LOGGER_NOT_ASSIGNED)
            has_assigned_logger = edge_case_type != "LOGGER_NOT_ASSIGNED"
            if has_assigned_logger:
                assgn_counter += 1
                self.shipment_loggers.append({
                    "assignment_id": f"ASSGN-2026-{assgn_counter:06d}",
                    "shipment_id": shipment_id,
                    "logger_id": logger_id,
                    "sensor_position": self.rng.choice(["PAYLOAD_CORE", "PAYLOAD_TOP_CORNER"]),
                    "activated_utc": format_iso_utc(actual_dep_dt - timedelta(minutes=10)),
                    "deactivated_utc": format_iso_utc(actual_arr_dt + timedelta(minutes=10)),
                    "assignment_status": "COMPLETED",
                })

            # Create 3 RouteStops: Origin (seq 1), Transit Hub (seq 2), Destination (seq 3)
            mid_dt = actual_dep_dt + timedelta(minutes=trip_duration_min // 2)
            is_dwell_scenario = scenario_category == "ROUTE_DWELL_EXCURSION"
            is_custody_gap_edge = edge_case_type == "CUSTODY_GAP_NOT_FLAGGED"

            hub_dwell_min = 90 if is_dwell_scenario else (150 if is_custody_gap_edge else 30)
            hub_flag = (
                "EXTENDED_TARMAC_DWELL"
                if is_dwell_scenario
                else ("CUSTOMS_HOLD_DELAY" if is_custody_gap_edge else "NORMAL_STOP")
            )

            stop1_id = f"STOP-{shipment_id}-01"
            stop2_id = f"STOP-{shipment_id}-02"
            stop3_id = f"STOP-{shipment_id}-03"

            stops = [
                {
                    "stop_id": stop1_id,
                    "shipment_id": shipment_id,
                    "sequence_no": 1,
                    "location_code": route_info["origin"],
                    "stop_type": "ORIGIN_WAREHOUSE",
                    "ambient_temp_c": route_info["ambient_origin_c"],
                    "has_cold_storage": True,
                    "planned_arrival_utc": format_iso_utc(planned_dep_dt - timedelta(minutes=30)),
                    "planned_departure_utc": format_iso_utc(planned_dep_dt),
                    "actual_arrival_utc": format_iso_utc(actual_dep_dt - timedelta(minutes=30)),
                    "actual_departure_utc": format_iso_utc(actual_dep_dt),
                    "dwell_duration_min": 30,
                    "dwell_event_flag": "NORMAL_STOP",
                },
                {
                    "stop_id": stop2_id,
                    "shipment_id": shipment_id,
                    "sequence_no": 2,
                    "location_code": route_info["hub"],
                    "stop_type": "AIRPORT_TARMAC" if route_info["mode"] != "ROAD_REEFER" else "CROSS_DOCK_HUB",
                    "ambient_temp_c": route_info["ambient_hub_c"],
                    "has_cold_storage": not is_dwell_scenario,
                    "planned_arrival_utc": format_iso_utc(mid_dt - timedelta(minutes=15)),
                    "planned_departure_utc": format_iso_utc(mid_dt + timedelta(minutes=15)),
                    "actual_arrival_utc": format_iso_utc(mid_dt - timedelta(minutes=hub_dwell_min // 2)),
                    "actual_departure_utc": format_iso_utc(mid_dt + timedelta(minutes=hub_dwell_min // 2)),
                    "dwell_duration_min": hub_dwell_min,
                    "dwell_event_flag": hub_flag,
                },
                {
                    "stop_id": stop3_id,
                    "shipment_id": shipment_id,
                    "sequence_no": 3,
                    "location_code": route_info["dest"],
                    "stop_type": "DESTINATION_FACILITY",
                    "ambient_temp_c": route_info["ambient_dest_c"],
                    "has_cold_storage": True,
                    "planned_arrival_utc": format_iso_utc(planned_arr_dt),
                    "planned_departure_utc": format_iso_utc(planned_arr_dt + timedelta(minutes=20)),
                    "actual_arrival_utc": format_iso_utc(actual_arr_dt),
                    "actual_departure_utc": format_iso_utc(actual_arr_dt + timedelta(minutes=20)),
                    "dwell_duration_min": 20,
                    "dwell_event_flag": "NORMAL_STOP",
                },
            ]
            self.route_stops.extend(stops)

            # Generate CustodyEvents across the 3 stops
            custody_handover_gaps: List[int] = []
            cust_events_spec = [
                (stop1_id, 1, "CARRIER_PICKUP", "SUPPLIER", "CARRIER_OPERATOR", actual_dep_dt, 15),
                (
                    stop2_id,
                    2,
                    "HUB_ARRIVAL_SCAN",
                    "CARRIER_OPERATOR",
                    "WAREHOUSE_OPERATOR",
                    mid_dt,
                    165 if is_custody_gap_edge else (45 if is_dwell_scenario else 20),
                ),
                (stop3_id, 3, "FINAL_RECEIPT", "CARRIER_OPERATOR", "WAREHOUSE_OPERATOR", actual_arr_dt, 15),
            ]

            for stop_id, seq_no, ev_type, from_role, to_role, ev_dt, gap_min in cust_events_spec:
                cust_counter += 1
                cust_id = f"CUST-2026-{cust_counter:07d}"
                custody_handover_gaps.append(gap_min)
                true_gap_flag = gap_min > CUSTODY_GAP_THRESHOLD_MIN
                # If EDGE_CUSTODY_GAP_NOT_FLAGGED: silently suppress the flag in the custody record!
                recorded_gap_flag = False if is_custody_gap_edge else true_gap_flag
                sig_hash = compute_signature_hash(f"{cust_id}:{shipment_id}:{stop_id}:{format_iso_utc(ev_dt)}")

                self.custody_events.append({
                    "custody_event_id": cust_id,
                    "shipment_id": shipment_id,
                    "stop_id": stop_id,
                    "sequence_no": seq_no,
                    "event_type": ev_type,
                    "from_actor_role": from_role,
                    "to_actor_role": to_role,
                    "event_timestamp_utc": format_iso_utc(ev_dt),
                    "handover_gap_min": gap_min,
                    "custody_gap_flagged": recorded_gap_flag,
                    "seal_intact": True,
                    "operator_signature_hash": sig_hash,
                })

            # Generate 10-minute TemperatureReadings
            num_steps = (trip_duration_min // 10) + 1
            trip_reading_points: List[ReadingPoint] = []
            target_c = product_row["target_temp_c"]

            # Determine skipped steps if telemetry gap scenario
            skipped_indices = set()
            if scenario_category == "TELEMETRY_GAP_FLAGGED" or edge_case_type == "MISSING_DATA_SILENTLY_PASSED":
                # Drop 5 consecutive 10-minute readings (creating a 50-minute telemetry gap > max_telemetry_gap_min)
                mid_step = num_steps // 2
                for s_idx in range(max(2, mid_step - 2), min(num_steps - 1, mid_step + 3)):
                    skipped_indices.add(s_idx)

            prev_recorded_dt: Optional[datetime] = None
            seq_counter = 0

            for step_i in range(num_steps):
                if step_i in skipped_indices:
                    continue

                seq_counter += 1
                point_dt = actual_dep_dt + timedelta(minutes=step_i * 10)
                ts_iso = format_iso_utc(point_dt)
                ts_compact = format_compact_ts(point_dt)
                reading_id = f"TEMP-{logger_id}-{ts_compact}"

                # Compute temperature profile based on scenario
                noise = self.rng.uniform(-0.35, 0.35)
                temp_c = round(target_c + noise, 2)

                if scenario_category == "MINOR_EXCURSION" and step_i in (4, 5):
                    # 20-minute minor excursion just above max_temp_c (within single excursion limit)
                    temp_c = round(true_policy.max_temp_c + 1.1, 2)
                elif scenario_category == "CRITICAL_EXCURSION" and 3 <= step_i <= 9:
                    # 70-minute critical excursion spiking above critical_high_temp_c
                    temp_c = round(true_policy.critical_high_temp_c + 1.8, 2)
                elif scenario_category == "ROUTE_DWELL_EXCURSION" and (num_steps // 2 - 2) <= step_i <= (num_steps // 2 + 3):
                    # Extended tarmac dwell causes thermal bleed beyond single excursion limit
                    temp_c = round(true_policy.max_temp_c + 2.6, 2)
                elif scenario_category == "CALIBRATION_BORDERLINE" and step_i == (num_steps // 2):
                    # Reading lands right inside the calibration uncertainty margin of max_temp_c
                    temp_c = round(true_policy.max_temp_c - (cal_unc * 0.5), 2)
                elif edge_case_type == "PRODUCT_THRESHOLD_MISMATCH":
                    # If assigned_policy has higher max_temp_c than true_policy, generate readings that breach
                    # true_policy (e.g. +3.5C above true max_temp_c) while staying below wrong assigned_policy!
                    if assigned_policy.max_temp_c > true_policy.max_temp_c and 3 <= step_i <= 7:
                        temp_c = round(true_policy.max_temp_c + 3.5, 2)
                    elif assigned_policy.max_temp_c < true_policy.max_temp_c:
                        # Normal target_c of product_row is already above assigned_policy.max_temp_c
                        temp_c = round(target_c + noise, 2)
                elif edge_case_type == "CROSS_SHIPMENT_EXCURSION_CALC" and step_i == 4:
                    # Only a 10-minute minor excursion on THIS shipment (acceptable on its own),
                    # whereas adding previous shipment's excursion pushes total over limit!
                    temp_c = round(true_policy.max_temp_c + 1.2, 2)

                elapsed_min = 0 if prev_recorded_dt is None else int((point_dt - prev_recorded_dt).total_seconds() // 60)
                prev_recorded_dt = point_dt

                is_exc_point = (temp_c < assigned_policy.min_temp_c) or (temp_c > assigned_policy.max_temp_c)
                sensor_status = "GAP_RECOVERED" if elapsed_min > 10 else "OK"

                # For EDGE_LOGGER_NOT_ASSIGNED: physical logger still recorded orphan telemetry (`shipment_id = None`),
                # or shipment has no linked telemetry. We record 3 orphan readings with `shipment_id = None` to test
                # orphan telemetry detection and FK integrity!
                if edge_case_type == "LOGGER_NOT_ASSIGNED":
                    if step_i < 3:
                        self.temperature_readings.append({
                            "reading_id": reading_id,
                            "logger_id": logger_id,
                            "shipment_id": None,
                            "sequence_no": seq_counter,
                            "recorded_at_utc": ts_iso,
                            "temperature_c": temp_c,
                            "ambient_estimate_c": route_info["ambient_origin_c"],
                            "calibration_uncertainty_c": cal_unc,
                            "elapsed_since_prev_min": elapsed_min,
                            "is_excursion_point": False,
                            "sensor_status": "OK",
                        })
                    continue

                self.temperature_readings.append({
                    "reading_id": reading_id,
                    "logger_id": logger_id,
                    "shipment_id": shipment_id,
                    "sequence_no": seq_counter,
                    "recorded_at_utc": ts_iso,
                    "temperature_c": temp_c,
                    "ambient_estimate_c": route_info["ambient_hub_c"],
                    "calibration_uncertainty_c": cal_unc,
                    "elapsed_since_prev_min": elapsed_min,
                    "is_excursion_point": is_exc_point,
                    "sensor_status": sensor_status,
                })

                trip_reading_points.append(
                    ReadingPoint(
                        reading_id=reading_id,
                        logger_id=logger_id,
                        shipment_id=shipment_id,
                        sequence_no=seq_counter,
                        recorded_at_utc=ts_iso,
                        temperature_c=temp_c,
                        calibration_uncertainty_c=cal_unc,
                        elapsed_since_prev_min=elapsed_min,
                    )
                )

            # Evaluate Ground-Truth Compliance (using canonical `true_policy`, true custody gaps, and single-shipment readings)
            true_eval = ComplianceEngine.evaluate_shipment(
                actual_departure_utc=format_iso_utc(actual_dep_dt),
                actual_arrival_utc=format_iso_utc(actual_arr_dt),
                policy=true_policy,
                readings=trip_reading_points,
                custody_handover_gaps_min=custody_handover_gaps,
                has_assigned_logger=has_assigned_logger,
                sampling_interval_min=10,
            )

            # Evaluate Reported ExcursionReport (which reflects the 5 injected negative edge cases when applicable)
            if edge_case_type == "PRODUCT_THRESHOLD_MISMATCH":
                # Evaluated against the wrong assigned_policy!
                reported_eval = ComplianceEngine.evaluate_shipment(
                    actual_departure_utc=format_iso_utc(actual_dep_dt),
                    actual_arrival_utc=format_iso_utc(actual_arr_dt),
                    policy=assigned_policy,
                    readings=trip_reading_points,
                    custody_handover_gaps_min=custody_handover_gaps,
                    has_assigned_logger=True,
                    sampling_interval_min=10,
                )
            elif edge_case_type == "MISSING_DATA_SILENTLY_PASSED":
                # Defect: Telemetry gap is NOT flagged and shipment is silently passed as COMPLIANT / AUTO_RELEASE!
                reported_eval = ComplianceEngine.evaluate_shipment(
                    actual_departure_utc=format_iso_utc(actual_dep_dt),
                    actual_arrival_utc=format_iso_utc(actual_arr_dt),
                    policy=true_policy,
                    readings=trip_reading_points,
                    custody_handover_gaps_min=custody_handover_gaps,
                    has_assigned_logger=True,
                    sampling_interval_min=10,
                )
                reported_eval = type(reported_eval)(
                    expected_reading_count=reported_eval.expected_reading_count,
                    actual_reading_count=reported_eval.actual_reading_count,
                    missing_reading_count=reported_eval.missing_reading_count,
                    max_telemetry_gap_min=reported_eval.max_telemetry_gap_min,
                    telemetry_gap_flagged=False,  # Silently unflagged!
                    custody_gap_flagged=reported_eval.custody_gap_flagged,
                    min_recorded_temp_c=reported_eval.min_recorded_temp_c,
                    max_recorded_temp_c=reported_eval.max_recorded_temp_c,
                    mean_kinetic_temp_c=reported_eval.mean_kinetic_temp_c,
                    total_excursion_duration_min=reported_eval.total_excursion_duration_min,
                    max_single_excursion_min=reported_eval.max_single_excursion_min,
                    uncertainty_borderline_flag=reported_eval.uncertainty_borderline_flag,
                    compliance_status="COMPLIANT",  # Silently passed!
                    disposition_decision="AUTO_RELEASE",
                    first_breach_reading_id=reported_eval.first_breach_reading_id,
                    evaluated_by_actor="AUTOMATED_POLICY_ENGINE",
                )
            elif edge_case_type == "CUSTODY_GAP_NOT_FLAGGED":
                # Defect: Custody gap > 120 min is ignored and not flagged in ExcursionReport!
                reported_eval = ComplianceEngine.evaluate_shipment(
                    actual_departure_utc=format_iso_utc(actual_dep_dt),
                    actual_arrival_utc=format_iso_utc(actual_arr_dt),
                    policy=true_policy,
                    readings=trip_reading_points,
                    custody_handover_gaps_min=[15, 20, 15],  # Suppressed gap
                    has_assigned_logger=True,
                    sampling_interval_min=10,
                )
            elif edge_case_type == "CROSS_SHIPMENT_EXCURSION_CALC":
                # Defect: Excursion duration is erroneously aggregated across unrelated shipments sharing the logger
                # (or contaminated with 120 minutes from another shipment's excursion window)!
                contaminated_extra_min = prev_shipment_excursion_min_by_logger.get(logger_id, 0)
                if contaminated_extra_min < 90:
                    contaminated_extra_min = 100  # Contaminated from unrelated shipment history
                inflated_total = true_eval.total_excursion_duration_min + contaminated_extra_min
                inflated_single = max(true_eval.max_single_excursion_min, contaminated_extra_min)
                reported_eval = type(true_eval)(
                    expected_reading_count=true_eval.expected_reading_count,
                    actual_reading_count=true_eval.actual_reading_count,
                    missing_reading_count=true_eval.missing_reading_count,
                    max_telemetry_gap_min=true_eval.max_telemetry_gap_min,
                    telemetry_gap_flagged=true_eval.telemetry_gap_flagged,
                    custody_gap_flagged=true_eval.custody_gap_flagged,
                    min_recorded_temp_c=true_eval.min_recorded_temp_c,
                    max_recorded_temp_c=true_eval.max_recorded_temp_c,
                    mean_kinetic_temp_c=true_eval.mean_kinetic_temp_c,
                    total_excursion_duration_min=inflated_total,
                    max_single_excursion_min=inflated_single,
                    uncertainty_borderline_flag=true_eval.uncertainty_borderline_flag,
                    compliance_status="CRITICAL_EXCURSION",
                    disposition_decision="REJECT_DESTROY",
                    first_breach_reading_id=true_eval.first_breach_reading_id,
                    evaluated_by_actor="OPERATIONS_MANAGER",
                )
            else:
                reported_eval = true_eval

            # Update logger's history tracker
            prev_shipment_excursion_min_by_logger[logger_id] = max(
                prev_shipment_excursion_min_by_logger.get(logger_id, 0),
                true_eval.total_excursion_duration_min,
            )

            # Determine final shipment lifecycle state from reported disposition decision
            if reported_eval.disposition_decision in ("AUTO_RELEASE", "RELEASE_WITH_STABILITY_DEDUCTION"):
                final_state = "CLOSED_RELEASED"
            elif reported_eval.disposition_decision == "REJECT_DESTROY":
                final_state = "CLOSED_REJECTED"
            else:
                final_state = "CLOSED_QUARANTINED"

            eval_dt = actual_arr_dt + timedelta(minutes=15)

            # Append Shipment record
            self.shipments.append({
                "shipment_id": shipment_id,
                "primary_product_id": prod_id,
                "assigned_policy_id": assigned_policy.policy_id,
                "packaging_id": pkg_row["packaging_id"],
                "supplier_actor": SUPPLIERS[(shp_idx - 1) % len(SUPPLIERS)],
                "planner_actor": PLANNERS[(shp_idx - 1) % len(PLANNERS)],
                "carrier_actor": CARRIERS[(shp_idx - 1) % len(CARRIERS)],
                "origin_location": route_info["origin"],
                "destination_location": route_info["dest"],
                "transport_mode": route_info["mode"],
                "planned_departure_utc": format_iso_utc(planned_dep_dt),
                "planned_arrival_utc": format_iso_utc(planned_arr_dt),
                "actual_departure_utc": format_iso_utc(actual_dep_dt),
                "actual_arrival_utc": format_iso_utc(actual_arr_dt),
                "total_distance_km": route_info["distance_km"],
                "declared_value_usd": declared_value_usd,
                "lifecycle_state": final_state,
            })

            # Append ExcursionReport record
            self.excursion_reports.append({
                "report_id": report_id,
                "shipment_id": shipment_id,
                "policy_id_evaluated": assigned_policy.policy_id,
                "logger_id_evaluated": logger_id if has_assigned_logger else None,
                "expected_reading_count": reported_eval.expected_reading_count,
                "actual_reading_count": reported_eval.actual_reading_count,
                "missing_reading_count": reported_eval.missing_reading_count,
                "max_telemetry_gap_min": reported_eval.max_telemetry_gap_min,
                "telemetry_gap_flagged": reported_eval.telemetry_gap_flagged,
                "custody_gap_flagged": reported_eval.custody_gap_flagged,
                "min_recorded_temp_c": reported_eval.min_recorded_temp_c,
                "max_recorded_temp_c": reported_eval.max_recorded_temp_c,
                "mean_kinetic_temp_c": reported_eval.mean_kinetic_temp_c,
                "total_excursion_duration_min": reported_eval.total_excursion_duration_min,
                "max_single_excursion_min": reported_eval.max_single_excursion_min,
                "uncertainty_borderline_flag": reported_eval.uncertainty_borderline_flag,
                "compliance_status": reported_eval.compliance_status,
                "disposition_decision": reported_eval.disposition_decision,
                "first_breach_reading_id": reported_eval.first_breach_reading_id,
                "evaluated_by_actor": reported_eval.evaluated_by_actor,
                "evaluated_at_utc": format_iso_utc(eval_dt),
            })

            # Persist explicit state transitions in ShipmentStateHistory (Rule 4: never overwrite history)
            transitions = [
                (None, "PLANNED", planned_dep_dt - timedelta(hours=12), "PLANNER", "SHIPMENT_SCHEDULED"),
                ("PLANNED", "PACKED", actual_dep_dt - timedelta(minutes=30), "SUPPLIER", "THERMAL_PACKOUT_VERIFIED"),
                ("PACKED", "IN_TRANSIT", actual_dep_dt, "CARRIER_OPERATOR", "DEPARTED_ORIGIN_FACILITY"),
                ("IN_TRANSIT", "DELIVERED", actual_arr_dt, "WAREHOUSE_OPERATOR", "ARRIVED_DESTINATION_DOCK"),
                ("DELIVERED", "EVALUATED", eval_dt - timedelta(minutes=5), "LOGISTICS_ANALYST", "TELEMETRY_POLICY_EVALUATED"),
                ("EVALUATED", final_state, eval_dt, "OPERATIONS_MANAGER", f"DISPOSITION_{reported_eval.disposition_decision}"),
            ]
            for seq_s, (p_state, n_state, chg_dt, actor_r, reason_c) in enumerate(transitions, start=1):
                sthist_counter += 1
                self.shipment_state_history.append({
                    "state_event_id": f"STHIST-2026-{sthist_counter:07d}",
                    "shipment_id": shipment_id,
                    "sequence_no": seq_s,
                    "prior_state": p_state,
                    "new_state": n_state,
                    "changed_at_utc": format_iso_utc(chg_dt),
                    "actor_role": actor_r,
                    "reason_code": reason_c,
                })

            # Build separate Ground Truth record (Step 7)
            root_cause_map = {
                "NORMAL_COMPLIANT": "All temperature readings and custody handoffs within product and route policy.",
                "MINOR_EXCURSION": "Brief thermal excursion within cumulative and single-excursion stability budget.",
                "CRITICAL_EXCURSION": "Sustained temperature breach exceeding critical threshold and allowed duration.",
                "ROUTE_DWELL_EXCURSION": "Extended airport tarmac dwell without cold storage caused thermal excursion.",
                "CALIBRATION_BORDERLINE": "Peak temperature lies within sensor calibration uncertainty band near threshold.",
                "TELEMETRY_GAP_FLAGGED": "Logger communication drop exceeded max_telemetry_gap_min; properly flagged.",
                "EDGE_LOGGER_NOT_ASSIGNED": "Defect injected: Logger was not bound to shipment in ShipmentLogger; zero linked readings.",
                "EDGE_PRODUCT_THRESHOLD_MISMATCH": (
                    f"Defect injected: Shipment bound to policy {assigned_policy.policy_id} instead of canonical {true_policy.policy_id}."
                ),
                "EDGE_MISSING_DATA_SILENTLY_PASSED": (
                    f"Defect injected: {true_eval.max_telemetry_gap_min}-min telemetry gap was silently unflagged and passed as COMPLIANT."
                ),
                "EDGE_CUSTODY_GAP_NOT_FLAGGED": (
                    f"Defect injected: {max(custody_handover_gaps)}-min custody delay at transit hub was not flagged in CustodyEvent or ExcursionReport."
                ),
                "EDGE_CROSS_SHIPMENT_EXCURSION_CALC": (
                    f"Defect injected: ExcursionReport duration ({reported_eval.total_excursion_duration_min} min) contaminated across unrelated shipments; true duration is {true_eval.total_excursion_duration_min} min."
                ),
            }

            self.ground_truth.append({
                "shipment_id": shipment_id,
                "scenario_category": scenario_category,
                "is_injected_edge_case": edge_case_type != "NONE",
                "edge_case_type": edge_case_type,
                "true_policy_id": true_policy.policy_id,
                "assigned_policy_id": assigned_policy.policy_id,
                "true_excursion_duration_min": true_eval.total_excursion_duration_min,
                "reported_excursion_duration_min": reported_eval.total_excursion_duration_min,
                "true_max_single_excursion_min": true_eval.max_single_excursion_min,
                "true_telemetry_gap_min": true_eval.max_telemetry_gap_min,
                "true_custody_gap_min": max(custody_handover_gaps),
                "true_compliance_status": true_eval.compliance_status,
                "true_disposition_decision": true_eval.disposition_decision,
                "reported_compliance_status": reported_eval.compliance_status,
                "reported_disposition_decision": reported_eval.disposition_decision,
                "root_cause_summary": root_cause_map[scenario_category],
            })


def compute_file_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def export_datasets(
    datasets: Dict[str, List[Dict[str, Any]]],
    output_dir: Path,
    seed: int,
    scale: str,
    export_sql: bool = True,
) -> Dict[str, Any]:
    """
    Export all generated tables to CSV, JSON, SQLite database + SQL seed script,
    and write `source_manifest.json` with exact row counts and SHA-256 checksums.
    Kept strictly deterministic so regenerating with the same seed yields identical checksums.
    """
    csv_dir = output_dir / "csv"
    json_dir = output_dir / "json"
    sql_dir = output_dir / "sql"
    gt_dir = output_dir / "ground_truth"

    for d in (csv_dir, json_dir, sql_dir, gt_dir):
        d.mkdir(parents=True, exist_ok=True)

    file_checksums: Dict[str, Dict[str, Any]] = {}

    for table_name, rows in datasets.items():
        if not rows:
            continue

        is_gt = table_name == "GroundTruth"
        target_csv = (gt_dir / "ground_truth.csv") if is_gt else (csv_dir / f"{table_name}.csv")
        target_json = (gt_dir / "ground_truth.json") if is_gt else (json_dir / f"{table_name}.json")

        # Write CSV with deterministic LF line endings
        fieldnames = list(rows[0].keys())
        with open(target_csv, "w", newline="\n", encoding="utf-8") as f_csv:
            writer = csv.DictWriter(f_csv, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

        # For large telemetry tables in full mode, still write JSON deterministically
        with open(target_json, "w", newline="\n", encoding="utf-8") as f_json:
            json.dump(rows, f_json, indent=2)
            f_json.write("\n")

        rel_csv = str(target_csv.relative_to(output_dir)).replace("\\", "/")
        rel_json = str(target_json.relative_to(output_dir)).replace("\\", "/")

        file_checksums[rel_csv] = {
            "table": table_name,
            "format": "csv",
            "row_count": len(rows),
            "sha256": compute_file_sha256(target_csv),
        }
        file_checksums[rel_json] = {
            "table": table_name,
            "format": "json",
            "row_count": len(rows),
            "sha256": compute_file_sha256(target_json),
        }

    if export_sql:
        sql_seed_path = sql_dir / "seed_data.sql"
        sqlite_db_path = sql_dir / "cold_chain_compliance.db"
        if sqlite_db_path.exists():
            sqlite_db_path.unlink()

        _write_sql_and_sqlite(datasets, sql_seed_path, sqlite_db_path)
        rel_sql = str(sql_seed_path.relative_to(output_dir)).replace("\\", "/")
        file_checksums[rel_sql] = {
            "table": "ALL_INPUT_TABLES",
            "format": "sql",
            "row_count": sum(len(v) for k, v in datasets.items() if k != "GroundTruth"),
            "sha256": compute_file_sha256(sql_seed_path),
        }

    manifest = {
        "project": "YottaFlex Cold-Chain Shipment Compliance & Deviation Management",
        "generator_version": "1.0.0",
        "seed": seed,
        "scale": scale,
        "provenance": {
            "data_origin": "100% synthetic deterministic generation",
            "schema_inspiration": [
                "USP <1079> Good Storage and Distribution Practices for Drug Products (Haynes Mean Kinetic Temperature formula)",
                "EU GDP (Good Distribution Practice) Guidelines 2013/C 343/01 for Medicinal Products",
                "WHO Technical Report Series No. 992 Annex 5 (Model guidance for storage and transport of time- and temperature-sensitive pharmaceutical products)",
            ],
            "license": "MIT / Public Domain Dedication for Synthetic Data",
            "contains_pii_or_confidential_data": False,
        },
        "table_row_counts": {k: len(v) for k, v in datasets.items()},
        "files": file_checksums,
    }

    manifest_path = output_dir / "source_manifest.json"
    with open(manifest_path, "w", newline="\n", encoding="utf-8") as f_man:
        json.dump(manifest, f_man, indent=2)
        f_man.write("\n")

    return manifest


def _write_sql_and_sqlite(
    datasets: Dict[str, List[Dict[str, Any]]],
    sql_seed_path: Path,
    sqlite_db_path: Path,
) -> None:
    """Create DDL schema, load input tables into SQLite, and write a deterministic SQL seed file."""
    ddl_statements = [
        """CREATE TABLE Product (
            product_id TEXT PRIMARY KEY,
            sku TEXT UNIQUE NOT NULL,
            name TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL,
            storage_regime TEXT NOT NULL,
            target_temp_c REAL NOT NULL,
            unit_value_usd REAL NOT NULL,
            humidity_sensitive INTEGER NOT NULL,
            lifecycle_state TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT
        );""",
        """CREATE TABLE ThresholdPolicy (
            policy_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL REFERENCES Product(product_id),
            version INTEGER NOT NULL,
            min_temp_c REAL NOT NULL,
            max_temp_c REAL NOT NULL,
            critical_low_temp_c REAL NOT NULL,
            critical_high_temp_c REAL NOT NULL,
            max_single_excursion_min INTEGER NOT NULL,
            max_cumulative_excursion_min INTEGER NOT NULL,
            max_mkt_c REAL NOT NULL,
            max_telemetry_gap_min INTEGER NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            lifecycle_state TEXT NOT NULL
        );""",
        """CREATE TABLE Packaging (
            packaging_id TEXT PRIMARY KEY,
            model_name TEXT UNIQUE NOT NULL,
            packaging_type TEXT NOT NULL,
            target_regime TEXT NOT NULL,
            validated_hold_hours INTEGER NOT NULL,
            insulation_r_value REAL NOT NULL,
            payload_capacity_liters REAL NOT NULL,
            reusable INTEGER NOT NULL,
            lifecycle_state TEXT NOT NULL
        );""",
        """CREATE TABLE Lot (
            lot_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL REFERENCES Product(product_id),
            manufacturing_site TEXT NOT NULL,
            manufactured_date TEXT NOT NULL,
            expiry_date TEXT NOT NULL,
            initial_quantity_units INTEGER NOT NULL,
            remaining_stability_budget_min INTEGER NOT NULL,
            lifecycle_state TEXT NOT NULL
        );""",
        """CREATE TABLE Logger (
            logger_id TEXT PRIMARY KEY,
            serial_number TEXT UNIQUE NOT NULL,
            manufacturer_model TEXT NOT NULL,
            sampling_interval_min INTEGER NOT NULL,
            calibration_uncertainty_c REAL NOT NULL,
            calibration_date TEXT NOT NULL,
            calibration_expiry_date TEXT NOT NULL,
            battery_pct INTEGER NOT NULL,
            firmware_version TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL
        );""",
        """CREATE TABLE Shipment (
            shipment_id TEXT PRIMARY KEY,
            primary_product_id TEXT NOT NULL REFERENCES Product(product_id),
            assigned_policy_id TEXT NOT NULL REFERENCES ThresholdPolicy(policy_id),
            packaging_id TEXT NOT NULL REFERENCES Packaging(packaging_id),
            supplier_actor TEXT NOT NULL,
            planner_actor TEXT NOT NULL,
            carrier_actor TEXT NOT NULL,
            origin_location TEXT NOT NULL,
            destination_location TEXT NOT NULL,
            transport_mode TEXT NOT NULL,
            planned_departure_utc TEXT NOT NULL,
            planned_arrival_utc TEXT NOT NULL,
            actual_departure_utc TEXT NOT NULL,
            actual_arrival_utc TEXT NOT NULL,
            total_distance_km REAL NOT NULL,
            declared_value_usd REAL NOT NULL,
            lifecycle_state TEXT NOT NULL
        );""",
        """CREATE TABLE ShipmentLot (
            shipment_lot_id TEXT PRIMARY KEY,
            shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id),
            lot_id TEXT NOT NULL REFERENCES Lot(lot_id),
            quantity_units INTEGER NOT NULL
        );""",
        """CREATE TABLE ShipmentLogger (
            assignment_id TEXT PRIMARY KEY,
            shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id),
            logger_id TEXT NOT NULL REFERENCES Logger(logger_id),
            sensor_position TEXT NOT NULL,
            activated_utc TEXT NOT NULL,
            deactivated_utc TEXT NOT NULL,
            assignment_status TEXT NOT NULL
        );""",
        """CREATE TABLE RouteStop (
            stop_id TEXT PRIMARY KEY,
            shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id),
            sequence_no INTEGER NOT NULL,
            location_code TEXT NOT NULL,
            stop_type TEXT NOT NULL,
            ambient_temp_c REAL NOT NULL,
            has_cold_storage INTEGER NOT NULL,
            planned_arrival_utc TEXT NOT NULL,
            planned_departure_utc TEXT NOT NULL,
            actual_arrival_utc TEXT NOT NULL,
            actual_departure_utc TEXT NOT NULL,
            dwell_duration_min INTEGER NOT NULL,
            dwell_event_flag TEXT NOT NULL
        );""",
        """CREATE TABLE CustodyEvent (
            custody_event_id TEXT PRIMARY KEY,
            shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id),
            stop_id TEXT NOT NULL REFERENCES RouteStop(stop_id),
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            from_actor_role TEXT NOT NULL,
            to_actor_role TEXT NOT NULL,
            event_timestamp_utc TEXT NOT NULL,
            handover_gap_min INTEGER NOT NULL,
            custody_gap_flagged INTEGER NOT NULL,
            seal_intact INTEGER NOT NULL,
            operator_signature_hash TEXT NOT NULL
        );""",
        """CREATE TABLE TemperatureReading (
            reading_id TEXT PRIMARY KEY,
            logger_id TEXT NOT NULL REFERENCES Logger(logger_id),
            shipment_id TEXT REFERENCES Shipment(shipment_id),
            sequence_no INTEGER NOT NULL,
            recorded_at_utc TEXT NOT NULL,
            temperature_c REAL NOT NULL,
            ambient_estimate_c REAL NOT NULL,
            calibration_uncertainty_c REAL NOT NULL,
            elapsed_since_prev_min INTEGER NOT NULL,
            is_excursion_point INTEGER NOT NULL,
            sensor_status TEXT NOT NULL
        );""",
        """CREATE TABLE ExcursionReport (
            report_id TEXT PRIMARY KEY,
            shipment_id TEXT UNIQUE NOT NULL REFERENCES Shipment(shipment_id),
            policy_id_evaluated TEXT NOT NULL REFERENCES ThresholdPolicy(policy_id),
            logger_id_evaluated TEXT REFERENCES Logger(logger_id),
            expected_reading_count INTEGER NOT NULL,
            actual_reading_count INTEGER NOT NULL,
            missing_reading_count INTEGER NOT NULL,
            max_telemetry_gap_min INTEGER NOT NULL,
            telemetry_gap_flagged INTEGER NOT NULL,
            custody_gap_flagged INTEGER NOT NULL,
            min_recorded_temp_c REAL,
            max_recorded_temp_c REAL,
            mean_kinetic_temp_c REAL,
            total_excursion_duration_min INTEGER NOT NULL,
            max_single_excursion_min INTEGER NOT NULL,
            uncertainty_borderline_flag INTEGER NOT NULL,
            compliance_status TEXT NOT NULL,
            disposition_decision TEXT NOT NULL,
            first_breach_reading_id TEXT REFERENCES TemperatureReading(reading_id),
            evaluated_by_actor TEXT NOT NULL,
            evaluated_at_utc TEXT NOT NULL
        );""",
        """CREATE TABLE ShipmentStateHistory (
            state_event_id TEXT PRIMARY KEY,
            shipment_id TEXT NOT NULL REFERENCES Shipment(shipment_id),
            sequence_no INTEGER NOT NULL,
            prior_state TEXT,
            new_state TEXT NOT NULL,
            changed_at_utc TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            reason_code TEXT NOT NULL
        );""",
    ]

    conn = sqlite3.connect(str(sqlite_db_path))
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")

    for ddl in ddl_statements:
        cur.execute(ddl)

    table_order = [
        "Product",
        "ThresholdPolicy",
        "Packaging",
        "Lot",
        "Logger",
        "Shipment",
        "ShipmentLot",
        "ShipmentLogger",
        "RouteStop",
        "CustodyEvent",
        "TemperatureReading",
        "ExcursionReport",
        "ShipmentStateHistory",
    ]

    for tbl in table_order:
        rows = datasets.get(tbl, [])
        if not rows:
            continue
        cols = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_list = ", ".join(cols)
        sql_ins = f"INSERT INTO {tbl} ({col_list}) VALUES ({placeholders})"
        values = [tuple(r[c] for c in cols) for r in rows]
        cur.executemany(sql_ins, values)

    conn.commit()

    # Write deterministic SQL seed script (DDL + master/transaction inserts; cap TemperatureReading inserts in text file if >10k rows while SQLite DB holds 100% of rows)
    with open(sql_seed_path, "w", newline="\n", encoding="utf-8") as f_sql:
        f_sql.write("-- Deterministic SQL Seed Dump for Cold-Chain Compliance Platform\n")
        f_sql.write("PRAGMA foreign_keys = ON;\nBEGIN TRANSACTION;\n\n")
        for ddl in ddl_statements:
            f_sql.write(ddl + "\n\n")

        for tbl in table_order:
            rows = datasets.get(tbl, [])
            if not rows:
                continue
            cols = list(rows[0].keys())
            col_list = ", ".join(cols)
            # Write all rows for starter or master tables; for full 80,000+ reading dump, write all master/shipment rows and first 2,500 readings to keep text SQL seed fast while SQLite DB & CSV have all 100%
            max_sql_rows = 2500 if (tbl == "TemperatureReading" and len(rows) > 10000) else len(rows)
            if max_sql_rows < len(rows):
                f_sql.write(
                    f"-- Note: Showing first {max_sql_rows} of {len(rows)} rows for {tbl} in text SQL file (100% of rows are loaded in cold_chain_compliance.db and csv/{tbl}.csv)\n"
                )
            for r in rows[:max_sql_rows]:
                formatted_vals = []
                for c in cols:
                    v = r[c]
                    if v is None:
                        formatted_vals.append("NULL")
                    elif isinstance(v, bool):
                        formatted_vals.append("1" if v else "0")
                    elif isinstance(v, (int, float)):
                        formatted_vals.append(str(v))
                    else:
                        escaped = str(v).replace("'", "''")
                        formatted_vals.append(f"'{escaped}'")
                f_sql.write(f"INSERT INTO {tbl} ({col_list}) VALUES ({', '.join(formatted_vals)});\n")
            f_sql.write("\n")
        f_sql.write("COMMIT;\n")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic Cold-Chain Compliance synthetic dataset.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Fixed RNG seed (default: 20261022)")
    parser.add_argument(
        "--scale",
        choices=["full", "starter"],
        default="full",
        help="Dataset scale: 'full' (5,000 shipments, 1,000 loggers) or 'starter' (250 shipments, 100 loggers)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/full",
        help="Directory to export CSV, JSON, SQL, and ground_truth files",
    )
    args = parser.parse_args()

    t0 = time.time()
    print(f"[Generator] Starting deterministic generation (seed={args.seed}, scale={args.scale})...")
    gen = SyntheticDataGenerator(seed=args.seed, scale=args.scale)
    datasets = gen.generate_all()

    out_path = Path(args.output_dir)
    manifest = export_datasets(datasets, out_path, seed=args.seed, scale=args.scale, export_sql=True)
    elapsed = time.time() - t0

    print(f"[Generator] Completed in {elapsed:.2f}s. Output directory: {out_path.resolve()}")
    for tbl, count in manifest["table_row_counts"].items():
        print(f"  - {tbl:<22}: {count:>8,} rows")


if __name__ == "__main__":
    main()
