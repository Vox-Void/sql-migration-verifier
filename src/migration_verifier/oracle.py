"""Independent expected results for named migration fixtures."""

import json
from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from migration_verifier.engines import ROOT
from migration_verifier.fixtures import fixture_rows


def expected(case_id: str, fixture_id: str) -> list[list[Any]]:
    if fixture_id == "repair":
        outcomes: dict[str, list[list[Any]]] = json.loads(
            (ROOT / "fixtures/expected-repair.json").read_bytes()
        )
        return outcomes[case_id]
    return reference(case_id, fixture_id)


def reference(case_id: str, fixture_id: str) -> list[list[Any]]:
    data = fixture_rows(fixture_id)
    auth_by_id = {row[0]: row for row in data["authorisations"]}
    captures = data["captures"]
    events = data["settlement_events"]
    totals: defaultdict[tuple[str, ...], Decimal] = defaultdict(Decimal)
    if case_id in {"Q01", "Q03"}:
        zones = {merchant: ZoneInfo(zone) for merchant, zone in data["merchants"]}
        for _, auth_id, value, amount in captures:
            _, merchant, _, _, currency, _ = auth_by_id[auth_id]
            timestamp = datetime.fromisoformat(value)
            if case_id == "Q03":
                day = timestamp.astimezone(zones[merchant]).date().isoformat()
            else:
                day = timestamp.date().isoformat()
            totals[(merchant, currency, day)] += Decimal(amount)
        return [[*key, amount] for key, amount in totals.items()]
    if case_id == "Q02":
        refunds: defaultdict[str, Decimal] = defaultdict(Decimal)
        for _, capture_id, _, amount in data["refunds"]:
            refunds[capture_id] += Decimal(amount)
        return [[cap_id, Decimal(amount) - refunds[cap_id]] for cap_id, _, _, amount in captures]
    if case_id == "Q04":
        return [[ref, amount] for _, merchant, _, _, ref, amount, _ in events if merchant == "M1"]
    if case_id == "Q05":
        by_merchant: defaultdict[str, list[list[Any]]] = defaultdict(list)
        for cap_id, auth_id, value, _ in captures:
            merchant = auth_by_id[auth_id][1]
            by_merchant[merchant].append([merchant, cap_id, value])
        ranked = []
        for merchant in sorted(by_merchant):
            by_id = sorted(by_merchant[merchant], key=lambda row: row[1])
            latest = sorted(by_id, key=lambda row: datetime.fromisoformat(row[2]), reverse=True)
            ranked.extend(latest[:3])
        return ranked
    if case_id == "Q06":
        cutoff = datetime.fromisoformat(data["migration_params"][0][0])
        latest_by_reference: dict[tuple[str, str], tuple[datetime, str, str]] = {}
        for event_id, merchant, _, _, ref, amount, recorded in events:
            timestamp = datetime.fromisoformat(recorded)
            if ref is not None and timestamp <= cutoff:
                key = (merchant, ref)
                candidate = (timestamp, event_id, amount)
                if key not in latest_by_reference or candidate[:2] > latest_by_reference[key][:2]:
                    latest_by_reference[key] = candidate
        return [[*key, record[1], record[2]] for key, record in latest_by_reference.items()]
    if case_id == "Q07":
        captured = {row[1] for row in captures}
        return [
            [auth_id, row[1]]
            for auth_id, row in auth_by_id.items()
            if row[5] == "APPROVED" and auth_id not in captured
        ]
    if case_id == "Q08":
        captured_amount: defaultdict[str, Decimal] = defaultdict(Decimal)
        for _, auth_id, _, amount in captures:
            captured_amount[auth_id] += Decimal(amount)
        return [
            [
                auth_id,
                (captured_amount[auth_id] / Decimal(row[3])).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
                if Decimal(row[3])
                else None,
            ]
            for auth_id, row in auth_by_id.items()
            if row[5] == "APPROVED"
        ]
    if case_id == "Q09":
        selected = [Decimal(row[3]) for row in data["authorisations"] if row[5] == "PENDING"]
        return [[len(selected), sum(selected, Decimal(0)) if selected else None]]
    if case_id == "Q10":
        right = next(row for row in events if row[0] == "E8")
        return [
            [row[0], right[0] if row[4] == right[4] else None]
            for row in events
            if row[0] in {"E4", "E5", "E6"}
        ]
    if case_id == "Q11":
        return [[row[4], row[5]] for row in events if row[0] in {"E0", "E1", "E2", "E3"}]
    if case_id == "Q12":
        start = datetime.fromisoformat("2026-01-07T21:59:59.999999+00:00")
        finish = datetime.fromisoformat("2026-01-07T22:00:00+00:00")
        return [
            [cap_id, value]
            for cap_id, _, value, _ in captures
            if start <= datetime.fromisoformat(value) <= finish
        ]
    raise ValueError(f"No oracle for {case_id}")
