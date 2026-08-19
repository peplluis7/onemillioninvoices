#!/usr/bin/env python3
"""Exact atomic-record temporal invoice clearing benchmark for 2022.

The implementation uses the manuscript's explicit common-day capacity rule:
    delta*_F(s) = max_t min_{e in F} c_e(t;s)
for every fixed offline path/circuit. The daily emulator fixes t to the current day.

No pandas/openpyxl are used. XLSX files are parsed directly from OOXML.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import heapq
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import networkx as nx
import numpy as np

PACKAGE_ROOT = Path(os.environ.get('TEMPORAL_INVOICE_ROOT', Path(__file__).resolve().parents[1]))
DATA_DIR = PACKAGE_ROOT
CSV_PATH = DATA_DIR / '!!2207 Invoices 650k Database(1).csv'
H2_FILES = [
    DATA_DIR / '2207 July 2022.xlsx',
    DATA_DIR / '2208 August 2022.xlsx',
    DATA_DIR / '2209 September 2022.xlsx',
    DATA_DIR / '2210 October 2022.xlsx',
    DATA_DIR / '2211 November 2022.xlsx',
    DATA_DIR / '2212 December 2022.xlsx',
]
START_2022 = date(2022, 1, 1)
END_2022 = date(2022, 12, 31)
HORIZON_2022 = 365

NS = {
    'a': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}


def parse_date_text(value: Any) -> Optional[date]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    token = s.split()[0]
    for fmt in ('%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            pass
    return None


def parse_excel_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        serial = float(s.replace(',', ''))
        if serial > 1000:
            return date(1899, 12, 30) + timedelta(days=int(serial))
    except ValueError:
        pass
    return parse_date_text(s)


def parse_amount_cents(value: Any) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip().replace('\xa0', '').replace(' ', '')
    if not s:
        return None
    if ',' in s and '.' in s:
        s = s.replace(',', '')
    elif ',' in s:
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = '.'.join(parts)
        else:
            s = ''.join(parts)
    try:
        x = float(s)
    except ValueError:
        return None
    if not math.isfinite(x):
        return None
    return int(round(x * 100))


def parse_cif(value: Any) -> Optional[int]:
    match = re.search(r'CIF\s*:\s*(?:RO\s*)?([0-9]+)', str(value or ''), flags=re.I)
    return int(match.group(1)) if match else None


def column_index(ref: str) -> int:
    match = re.match(r'([A-Z]+)', ref)
    if not match:
        raise ValueError(f'Bad cell reference: {ref}')
    result = 0
    for ch in match.group(1):
        result = result * 26 + ord(ch) - 64
    return result - 1


def normalize_ooxml_target(target: str) -> str:
    if target.startswith('/'):
        return target.lstrip('/')
    if target.startswith('xl/'):
        return target
    return 'xl/' + target


def iter_xlsx_rows(path: Path) -> Iterator[Tuple[int, Dict[str, str], str]]:
    """Yield row number, header-keyed values, and sheet name from first non-summary sheet."""
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        shared_strings: List[str] = []
        if 'xl/sharedStrings.xml' in names:
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            for si in root.findall('a:si', NS):
                shared_strings.append(''.join(t.text or '' for t in si.iter(f"{{{NS['a']}}}t")))

        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        rels_root = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        relationships = {
            rel.attrib['Id']: normalize_ooxml_target(rel.attrib['Target'])
            for rel in rels_root
        }
        sheets: List[Tuple[str, str]] = []
        for sheet in workbook.find('a:sheets', NS):
            rel_id = sheet.attrib[f"{{{NS['r']}}}id"]
            sheets.append((sheet.attrib['name'], relationships[rel_id]))

        for sheet_name, target in sheets:
            if sheet_name.lower() == 'summary':
                continue
            root = ET.fromstring(archive.read(target))
            sheet_data = root.find('a:sheetData', NS)
            if sheet_data is None:
                continue
            rows: List[List[str]] = []
            row_numbers: List[int] = []
            for row in sheet_data:
                values: Dict[int, str] = {}
                for cell in row.findall('a:c', NS):
                    idx = column_index(cell.attrib.get('r', 'A1'))
                    cell_type = cell.attrib.get('t')
                    v = cell.find('a:v', NS)
                    inline = cell.find('a:is', NS)
                    if cell_type == 's' and v is not None:
                        value = shared_strings[int(v.text)]
                    elif cell_type == 'inlineStr' and inline is not None:
                        value = ''.join(t.text or '' for t in inline.iter(f"{{{NS['a']}}}t"))
                    elif v is not None:
                        value = v.text or ''
                    else:
                        value = ''
                    values[idx] = value
                if values:
                    arr = [''] * (max(values) + 1)
                    for idx, value in values.items():
                        arr[idx] = value
                    rows.append(arr)
                    row_numbers.append(int(row.attrib.get('r', len(rows))))
            if not rows:
                return
            headers = [str(x).strip() for x in rows[0]]
            for row_number, arr in zip(row_numbers[1:], rows[1:]):
                arr = arr + [''] * (len(headers) - len(arr))
                yield row_number, dict(zip(headers, arr)), sheet_name
            return


@dataclass(frozen=True)
class AtomicRecord:
    uid: str
    debtor: str
    creditor: str
    amount: int
    issue_idx: int
    due_idx: int
    issue_ord: int
    due_ord: int
    status: str
    source: str
    fingerprint: str


@dataclass
class LoadAudit:
    source: str
    raw_rows: int = 0
    retained_rows: int = 0
    retained_amount: int = 0
    removed_bad_amount: int = 0
    removed_bad_party: int = 0
    removed_self: int = 0
    removed_bad_issue: int = 0
    removed_bad_due: int = 0
    removed_negative_maturity: int = 0
    removed_cancelled: int = 0
    removed_outside_period: int = 0
    removed_validation: int = 0
    duplicate_groups: int = 0
    duplicate_excess_rows: int = 0
    duplicate_excess_amount: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def match_key(issue: date, due: Optional[date], amount: Optional[int], series: str, number: str) -> Tuple[Any, ...]:
    return (issue, due, amount, series.strip().upper(), number.strip())


def build_verified_july_crosswalk() -> Tuple[Dict[str, int], Dict[str, Any]]:
    """Match 1-8 July records between the database snapshot and July workbook.

    Only one-to-one exact matches on issue date, due date, cent amount, series, and
    invoice number are used. The result maps internal database company IDs to CIFs.
    """
    xlsx_index: Dict[Tuple[Any, ...], List[Tuple[int, int]]] = defaultdict(list)
    for _, row, _ in iter_xlsx_rows(H2_FILES[0]):
        issue = parse_excel_date(row.get('Data'))
        due = parse_excel_date(row.get('Data Scadenta'))
        amount = parse_amount_cents(row.get('Valoare'))
        debtor = parse_cif(row.get('Client'))
        creditor = parse_cif(row.get('Furnizor'))
        if issue is None or debtor is None or creditor is None:
            continue
        key = match_key(issue, due, amount, str(row.get('Serie', '')), str(row.get('Numar', '')))
        xlsx_index[key].append((debtor, creditor))

    csv_index: Dict[Tuple[Any, ...], List[Tuple[str, str]]] = defaultdict(list)
    with CSV_PATH.open('r', encoding='utf-8-sig', errors='replace', newline='') as handle:
        for row in csv.reader(handle, delimiter=';'):
            if len(row) < 16:
                continue
            issue = parse_date_text(row[13])
            if issue is None or not (date(2022, 7, 1) <= issue <= date(2022, 7, 8)):
                continue
            due = parse_date_text(row[14])
            amount = parse_amount_cents(row[9])
            key = match_key(issue, due, amount, row[7], row[8])
            csv_index[key].append((row[6].strip(), row[5].strip()))  # debtor, creditor

    evidence: Dict[str, Counter] = defaultdict(Counter)
    unique_matches = 0
    ambiguous_keys = 0
    for key in set(xlsx_index).intersection(csv_index):
        if len(xlsx_index[key]) != 1 or len(csv_index[key]) != 1:
            ambiguous_keys += 1
            continue
        debtor_cif, creditor_cif = xlsx_index[key][0]
        debtor_id, creditor_id = csv_index[key][0]
        evidence[debtor_id][debtor_cif] += 1
        evidence[creditor_id][creditor_cif] += 1
        unique_matches += 1

    resolved: Dict[str, int] = {}
    conflicts: Dict[str, Dict[int, int]] = {}
    for internal_id, counts in evidence.items():
        if len(counts) == 1:
            resolved[internal_id] = next(iter(counts))
        else:
            conflicts[internal_id] = dict(counts)

    audit = {
        'one_to_one_invoice_matches': unique_matches,
        'resolved_internal_ids': len(resolved),
        'ambiguous_invoice_keys': ambiguous_keys,
        'conflicting_internal_ids': conflicts,
    }
    return resolved, audit


def stable_hash(parts: Sequence[Any]) -> str:
    text = '\x1f'.join('' if x is None else str(x) for x in parts)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_h1_2022(
    crosswalk: Dict[str, int],
    linkage: str,
    validated_only: bool,
    deduplicate: bool,
) -> Tuple[List[AtomicRecord], LoadAudit]:
    audit = LoadAudit(source='2022-H1 longitudinal database')
    provisional: List[AtomicRecord] = []
    seen: set[str] = set()
    duplicate_counts: Counter[str] = Counter()
    duplicate_amount: Dict[str, int] = {}

    def node(internal_id: str) -> str:
        if linkage == 'verified' and internal_id in crosswalk:
            return f'cif:{crosswalk[internal_id]}'
        return f'db:{internal_id}'

    with CSV_PATH.open('r', encoding='utf-8-sig', errors='replace', newline='') as handle:
        for line_number, row in enumerate(csv.reader(handle, delimiter=';'), start=1):
            if len(row) < 16:
                continue
            issue = parse_date_text(row[13])
            if issue is None or issue < START_2022 or issue > date(2022, 6, 30):
                continue
            audit.raw_rows += 1
            amount = parse_amount_cents(row[9])
            if amount is None or amount <= 0:
                audit.removed_bad_amount += 1
                continue
            due = parse_date_text(row[14])
            if due is None:
                audit.removed_bad_due += 1
                continue
            if due < issue:
                audit.removed_negative_maturity += 1
                continue
            if row[15].strip() == '1':
                audit.removed_cancelled += 1
                continue
            debtor_internal = row[6].strip()
            creditor_internal = row[5].strip()
            if not debtor_internal or not creditor_internal:
                audit.removed_bad_party += 1
                continue
            if debtor_internal == creditor_internal:
                audit.removed_self += 1
                continue
            if validated_only and not (row[11].strip().upper() == 'Y' and row[12].strip().upper() == 'Y'):
                audit.removed_validation += 1
                continue

            status = f"{row[11].strip().upper()}/{row[12].strip().upper()}"
            fp_parts = (
                debtor_internal,
                creditor_internal,
                row[7].strip().upper(),
                row[8].strip(),
                issue.isoformat(),
                due.isoformat(),
                amount,
                status,
            )
            fingerprint = stable_hash(fp_parts)
            duplicate_counts[fingerprint] += 1
            duplicate_amount[fingerprint] = amount
            if deduplicate and fingerprint in seen:
                continue
            seen.add(fingerprint)
            due_idx = min((due - START_2022).days, HORIZON_2022 - 1)
            provisional.append(
                AtomicRecord(
                    uid=f"db:{(int(row[0]) if row[0].strip().isdigit() else line_number):012d}:{line_number:09d}",
                    debtor=node(debtor_internal),
                    creditor=node(creditor_internal),
                    amount=amount,
                    issue_idx=(issue - START_2022).days,
                    due_idx=due_idx,
                    issue_ord=issue.toordinal(),
                    due_ord=due.toordinal(),
                    status=status,
                    source='longitudinal_csv',
                    fingerprint=fingerprint,
                )
            )

    duplicate_groups = {k: n for k, n in duplicate_counts.items() if n > 1}
    audit.duplicate_groups = len(duplicate_groups)
    audit.duplicate_excess_rows = sum(n - 1 for n in duplicate_groups.values())
    audit.duplicate_excess_amount = sum((n - 1) * duplicate_amount[k] for k, n in duplicate_groups.items())
    audit.retained_rows = len(provisional)
    audit.retained_amount = sum(r.amount for r in provisional)
    return provisional, audit


def load_h2_2022(validated_only: bool, deduplicate: bool) -> Tuple[List[AtomicRecord], LoadAudit]:
    audit = LoadAudit(source='2022-H2 monthly Excel workbooks')
    provisional: List[AtomicRecord] = []
    seen: set[str] = set()
    duplicate_counts: Counter[str] = Counter()
    duplicate_amount: Dict[str, int] = {}

    for path in H2_FILES:
        for row_number, row, sheet_name in iter_xlsx_rows(path):
            audit.raw_rows += 1
            amount = parse_amount_cents(row.get('Valoare'))
            if amount is None or amount <= 0:
                audit.removed_bad_amount += 1
                continue
            debtor_cif = parse_cif(row.get('Client'))
            creditor_cif = parse_cif(row.get('Furnizor'))
            if debtor_cif is None or creditor_cif is None:
                audit.removed_bad_party += 1
                continue
            if debtor_cif == creditor_cif:
                audit.removed_self += 1
                continue
            issue = parse_excel_date(row.get('Data'))
            if issue is None:
                audit.removed_bad_issue += 1
                continue
            if issue.year != 2022 or issue < date(2022, 7, 1) or issue > END_2022:
                audit.removed_outside_period += 1
                continue
            due = parse_excel_date(row.get('Data Scadenta'))
            if due is None:
                audit.removed_bad_due += 1
                continue
            if due < issue:
                audit.removed_negative_maturity += 1
                continue
            status = str(row.get('Stare', '')).strip()
            if validated_only and status.lower() != 'validata':
                audit.removed_validation += 1
                continue
            source_file = str(row.get('Source File', '')).strip() or path.name
            fp_parts = (
                debtor_cif,
                creditor_cif,
                str(row.get('Serie', '')).strip().upper(),
                str(row.get('Numar', '')).strip(),
                issue.isoformat(),
                due.isoformat(),
                amount,
                status,
            )
            fingerprint = stable_hash(fp_parts)
            duplicate_counts[fingerprint] += 1
            duplicate_amount[fingerprint] = amount
            if deduplicate and fingerprint in seen:
                continue
            seen.add(fingerprint)
            provisional.append(
                AtomicRecord(
                    uid=f'xlsx:{path.name}:{sheet_name}:{row_number:09d}',
                    debtor=f'cif:{debtor_cif}',
                    creditor=f'cif:{creditor_cif}',
                    amount=amount,
                    issue_idx=(issue - START_2022).days,
                    due_idx=min((due - START_2022).days, HORIZON_2022 - 1),
                    issue_ord=issue.toordinal(),
                    due_ord=due.toordinal(),
                    status=status,
                    source=source_file,
                    fingerprint=fingerprint,
                )
            )

    duplicate_groups = {k: n for k, n in duplicate_counts.items() if n > 1}
    audit.duplicate_groups = len(duplicate_groups)
    audit.duplicate_excess_rows = sum(n - 1 for n in duplicate_groups.values())
    audit.duplicate_excess_amount = sum((n - 1) * duplicate_amount[k] for k, n in duplicate_groups.items())
    audit.retained_rows = len(provisional)
    audit.retained_amount = sum(r.amount for r in provisional)
    return provisional, audit


def load_2022_dataset(
    segment: str = 'full',
    linkage: str = 'verified',
    validated_only: bool = False,
    deduplicate: bool = False,
) -> Tuple[List[AtomicRecord], Dict[str, Any]]:
    crosswalk, crosswalk_audit = build_verified_july_crosswalk()
    records: List[AtomicRecord] = []
    audits: List[LoadAudit] = []
    if segment in ('full', 'h1'):
        h1, audit_h1 = load_h1_2022(crosswalk, linkage, validated_only, deduplicate)
        records.extend(h1)
        audits.append(audit_h1)
    if segment in ('full', 'h2'):
        h2, audit_h2 = load_h2_2022(validated_only, deduplicate)
        records.extend(h2)
        audits.append(audit_h2)
    audit = {
        'segment': segment,
        'linkage': linkage,
        'validated_only': validated_only,
        'deduplicate': deduplicate,
        'crosswalk': crosswalk_audit,
        'sources': [a.to_dict() for a in audits],
        'records': len(records),
        'amount': sum(r.amount for r in records),
        'firms': len({v for r in records for v in (r.debtor, r.creditor)}),
        'edges': len({(r.debtor, r.creditor) for r in records}),
    }
    return records, audit


class State:
    """Mutable residual state with exact edge-day capacities."""

    def __init__(self, records: Sequence[AtomicRecord], horizon: int = HORIZON_2022):
        self.horizon = horizon
        self.initial_mass = sum(r.amount for r in records)
        def node_order(label: str) -> Tuple[int, int, str]:
            namespace, _, token = label.partition(':')
            try:
                numeric = int(token)
            except ValueError:
                numeric = 0
            namespace_rank = 0 if namespace == 'cif' else 1
            return (numeric, namespace_rank, label)

        nodes = sorted({v for r in records for v in (r.debtor, r.creditor)}, key=node_order)
        self.node_to_int = {node: idx for idx, node in enumerate(nodes)}
        self.int_to_node = nodes
        groups: Dict[Tuple[int, int], List[AtomicRecord]] = defaultdict(list)
        for record in records:
            groups[(self.node_to_int[record.debtor], self.node_to_int[record.creditor])].append(record)
        self.edge_map: Dict[Tuple[int, int], int] = {}
        self.edges: List[Dict[str, Any]] = []
        for edge_id, (uv, edge_records) in enumerate(sorted(groups.items())):
            self.edge_map[uv] = edge_id
            mutable_records = [
                [
                    r.amount,
                    r.issue_idx,
                    r.due_idx,
                    r.issue_ord,
                    r.due_ord,
                    r.uid,
                ]
                for r in edge_records
            ]
            # Earliest due, then earliest issue, then stable source identifier.
            mutable_records.sort(key=lambda x: (x[4], x[3], x[5]))
            diff = np.zeros(horizon + 1, dtype=np.int64)
            for amount, issue_idx, due_idx, *_ in mutable_records:
                diff[issue_idx] += amount
                diff[due_idx + 1] -= amount
            capacity = np.cumsum(diff[:-1])
            self.edges.append(
                {
                    'u': uv[0],
                    'v': uv[1],
                    'records': mutable_records,
                    'cap': capacity,
                    'version': 0,
                    'total': sum(x[0] for x in mutable_records),
                }
            )

    def copy(self) -> 'State':
        obj = object.__new__(State)
        obj.horizon = self.horizon
        obj.initial_mass = self.initial_mass
        obj.node_to_int = self.node_to_int.copy()
        obj.int_to_node = self.int_to_node.copy()
        obj.edge_map = self.edge_map.copy()
        obj.edges = []
        for edge in self.edges:
            obj.edges.append(
                {
                    'u': edge['u'],
                    'v': edge['v'],
                    'records': [row.copy() for row in edge['records']],
                    'cap': edge['cap'].copy(),
                    'version': 0,
                    'total': edge['total'],
                }
            )
        return obj

    def pair_common_day_capacity(self, edge_1: int, edge_2: int) -> Tuple[int, int]:
        matched = np.minimum(self.edges[edge_1]['cap'], self.edges[edge_2]['cap'])
        day = int(matched.argmax())
        return int(matched[day]), day

    def cycle_common_day_capacity(self, edge_ids: Sequence[int]) -> Tuple[int, int]:
        matched = self.edges[edge_ids[0]]['cap'].copy()
        for edge_id in edge_ids[1:]:
            np.minimum(matched, self.edges[edge_id]['cap'], out=matched)
        day = int(matched.argmax())
        return int(matched[day]), day

    def consume(self, edge_id: int, day: int, amount: int) -> List[Tuple[str, int, int, int, int, int]]:
        edge = self.edges[edge_id]
        remaining = amount
        fragments: List[Tuple[str, int, int, int, int, int]] = []
        for record in edge['records']:
            if remaining <= 0:
                break
            residual, issue_idx, due_idx, issue_ord, due_ord, uid = record
            if residual > 0 and issue_idx <= day <= due_idx:
                consumed = min(residual, remaining)
                record[0] -= consumed
                remaining -= consumed
                edge['total'] -= consumed
                edge['cap'][issue_idx : due_idx + 1] -= consumed
                fragments.append((uid, consumed, issue_idx, due_idx, issue_ord, due_ord))
        if remaining != 0:
            raise RuntimeError(
                f'Unable to consume {amount} on edge {edge_id}, day {day}; remaining {remaining}'
            )
        edge['version'] += 1
        return fragments

    def residual_mass(self) -> int:
        return sum(edge['total'] for edge in self.edges)


def enumerate_cycles(base: State, length_bound: int = 8) -> List[Tuple[Tuple[int, ...], Tuple[int, ...]]]:
    graph = nx.DiGraph()
    graph.add_edges_from((edge['u'], edge['v']) for edge in base.edges)
    unique: Dict[Tuple[int, ...], Tuple[int, ...]] = {}
    for node_cycle in nx.simple_cycles(graph, length_bound=length_bound):
        n = len(node_cycle)
        rotation = min(range(n), key=lambda i: tuple(node_cycle[i:] + node_cycle[:i]))
        canonical = tuple(node_cycle[rotation:] + node_cycle[:rotation])
        edge_ids = tuple(
            base.edge_map[(canonical[i], canonical[(i + 1) % n])]
            for i in range(n)
        )
        unique[canonical] = edge_ids
    return sorted(unique.items(), key=lambda item: (len(item[0]), item[0]))


def path_daily(base: State, keep_logs: bool = True) -> Dict[str, Any]:
    state = base.copy()
    start_time = time.time()
    incoming: Dict[int, List[int]] = defaultdict(list)
    outgoing: Dict[int, List[int]] = defaultdict(list)
    for edge_id, edge in enumerate(state.edges):
        outgoing[edge['u']].append(edge_id)
        incoming[edge['v']].append(edge_id)
    nodes = sorted(set(incoming).union(outgoing))
    operations = 0
    pmr = 0
    compression = 0
    instruction_mass = 0
    curve: List[int] = []
    logs: List[Dict[str, Any]] = []
    acceleration_numerator = 0
    positive_acceleration_numerator = 0
    positive_acceleration_mass = 0

    def pop_current(heap: List[Tuple[int, int, int]], day: int) -> Optional[int]:
        while heap:
            neg_capacity, _, edge_id = heapq.heappop(heap)
            current = int(state.edges[edge_id]['cap'][day])
            if current <= 0:
                continue
            if -neg_capacity != current:
                # A fresh entry was inserted immediately after the update that
                # made this one stale; discard the old entry without duplicating it.
                continue
            return edge_id
        return None

    for day in range(state.horizon):
        for intermediary in nodes:
            in_edges = incoming.get(intermediary, [])
            out_edges = outgoing.get(intermediary, [])
            if not in_edges or not out_edges:
                continue
            in_heap: List[Tuple[int, int, int]] = []
            out_heap: List[Tuple[int, int, int]] = []
            for edge_id in in_edges:
                capacity = int(state.edges[edge_id]['cap'][day])
                if capacity > 0:
                    heapq.heappush(in_heap, (-capacity, state.edges[edge_id]['u'], edge_id))
            for edge_id in out_edges:
                capacity = int(state.edges[edge_id]['cap'][day])
                if capacity > 0:
                    heapq.heappush(out_heap, (-capacity, state.edges[edge_id]['v'], edge_id))
            while True:
                best_in = pop_current(in_heap, day)
                if best_in is None:
                    break
                best_out = pop_current(out_heap, day)
                if best_out is None:
                    break
                in_capacity = int(state.edges[best_in]['cap'][day])
                out_capacity = int(state.edges[best_out]['cap'][day])
                amount = min(in_capacity, out_capacity)
                payer = state.edges[best_in]['u']
                payee = state.edges[best_out]['v']
                in_fragments = state.consume(best_in, day, amount)
                out_fragments = state.consume(best_out, day, amount)
                operations += 1
                compression += 2 * amount
                bilateral = payer == payee
                if bilateral:
                    pmr += 2 * amount
                else:
                    pmr += amount
                    instruction_mass += amount
                    i = j = 0
                    rem_i = in_fragments[0][1]
                    rem_j = out_fragments[0][1]
                    while i < len(in_fragments) and j < len(out_fragments):
                        paired = min(rem_i, rem_j)
                        due_in = in_fragments[i][5]
                        due_out = out_fragments[j][5]
                        acceleration = max(0, due_in - due_out)
                        acceleration_numerator += paired * acceleration
                        if acceleration > 0:
                            positive_acceleration_numerator += paired * acceleration
                            positive_acceleration_mass += paired
                        rem_i -= paired
                        rem_j -= paired
                        if rem_i == 0:
                            i += 1
                            rem_i = in_fragments[i][1] if i < len(in_fragments) else 0
                        if rem_j == 0:
                            j += 1
                            rem_j = out_fragments[j][1] if j < len(out_fragments) else 0
                if keep_logs:
                    logs.append(
                        {
                            'kind': 'path',
                            'day': day,
                            'amount': amount,
                            'payer': payer,
                            'intermediary': intermediary,
                            'payee': payee,
                            'in_edge': best_in,
                            'out_edge': best_out,
                            'bilateral': bilateral,
                            'in_fragments': in_fragments,
                            'out_fragments': out_fragments,
                        }
                    )
                # Reinsert selected edges with their updated capacities. Old entries,
                # if any, are discarded lazily by pop_current.
                new_in = int(state.edges[best_in]['cap'][day])
                if new_in > 0:
                    heapq.heappush(in_heap, (-new_in, state.edges[best_in]['u'], best_in))
                new_out = int(state.edges[best_out]['cap'][day])
                if new_out > 0:
                    heapq.heappush(out_heap, (-new_out, state.edges[best_out]['v'], best_out))
        curve.append(pmr)

    return {
        'method': 'path_daily',
        'operations': operations,
        'pmr': pmr,
        'compression': compression,
        'instruction_mass': instruction_mass,
        'residual_mass': state.residual_mass(),
        'runtime_seconds': time.time() - start_time,
        'curve': curve,
        'logs': logs,
        'mean_acceleration_days': acceleration_numerator / instruction_mass if instruction_mass else 0.0,
        'positive_only_mean_days': (
            positive_acceleration_numerator / positive_acceleration_mass
            if positive_acceleration_mass
            else 0.0
        ),
        'accelerated_mass_share': (
            positive_acceleration_mass / instruction_mass if instruction_mass else 0.0
        ),
    }


def cycle_daily(base: State, cycles: Sequence[Tuple[Tuple[int, ...], Tuple[int, ...]]], keep_logs: bool = True) -> Dict[str, Any]:
    state = base.copy()
    start_time = time.time()
    operations = 0
    pmr = 0
    curve: List[int] = []
    logs: List[Dict[str, Any]] = []

    for day in range(state.horizon):
        heap: List[Tuple[Any, ...]] = []
        for cycle_index, (nodes, edge_ids) in enumerate(cycles):
            amount = min(int(state.edges[edge_id]['cap'][day]) for edge_id in edge_ids)
            if amount <= 0:
                continue
            versions = tuple(state.edges[edge_id]['version'] for edge_id in edge_ids)
            heapq.heappush(
                heap,
                (-len(nodes) * amount, len(nodes), nodes, cycle_index, amount, versions),
            )
        while heap:
            _, length, nodes, cycle_index, amount, versions = heapq.heappop(heap)
            edge_ids = cycles[cycle_index][1]
            current_versions = tuple(state.edges[edge_id]['version'] for edge_id in edge_ids)
            if current_versions != versions:
                refreshed = min(int(state.edges[edge_id]['cap'][day]) for edge_id in edge_ids)
                if refreshed > 0:
                    heapq.heappush(
                        heap,
                        (-length * refreshed, length, nodes, cycle_index, refreshed, current_versions),
                    )
                continue
            refreshed = min(int(state.edges[edge_id]['cap'][day]) for edge_id in edge_ids)
            if refreshed != amount:
                if refreshed > 0:
                    heapq.heappush(
                        heap,
                        (-length * refreshed, length, nodes, cycle_index, refreshed, current_versions),
                    )
                continue
            operation_fragments = []
            for edge_id in edge_ids:
                operation_fragments.append((edge_id, state.consume(edge_id, day, amount)))
            operations += 1
            pmr += length * amount
            if keep_logs:
                logs.append(
                    {
                        'kind': 'cycle',
                        'day': day,
                        'amount': amount,
                        'nodes': nodes,
                        'edges': edge_ids,
                        'edge_fragments': operation_fragments,
                    }
                )
        curve.append(pmr)

    return {
        'method': 'cycle_daily',
        'operations': operations,
        'pmr': pmr,
        'compression': pmr,
        'instruction_mass': 0,
        'residual_mass': state.residual_mass(),
        'runtime_seconds': time.time() - start_time,
        'curve': curve,
        'logs': logs,
    }


def path_offline(base: State, keep_logs: bool = True) -> Dict[str, Any]:
    state = base.copy()
    start_time = time.time()
    incoming: Dict[int, List[int]] = defaultdict(list)
    outgoing: Dict[int, List[int]] = defaultdict(list)
    for edge_id, edge in enumerate(state.edges):
        outgoing[edge['u']].append(edge_id)
        incoming[edge['v']].append(edge_id)
    operations = 0
    pmr = 0
    compression = 0
    instruction_mass = 0
    logs: List[Dict[str, Any]] = []
    acceleration_numerator = 0
    positive_acceleration_numerator = 0
    positive_acceleration_mass = 0

    for intermediary in sorted(set(incoming).union(outgoing)):
        in_edges = incoming.get(intermediary, [])
        out_edges = outgoing.get(intermediary, [])
        if not in_edges or not out_edges:
            continue
        heap: List[Tuple[Any, ...]] = []
        for in_edge in in_edges:
            for out_edge in out_edges:
                capacity, day = state.pair_common_day_capacity(in_edge, out_edge)
                if capacity <= 0:
                    continue
                payer = state.edges[in_edge]['u']
                payee = state.edges[out_edge]['v']
                heapq.heappush(
                    heap,
                    (
                        -capacity,
                        day,
                        payer,
                        payee,
                        in_edge,
                        out_edge,
                        state.edges[in_edge]['version'],
                        state.edges[out_edge]['version'],
                    ),
                )
        while heap:
            neg_capacity, day, payer, payee, in_edge, out_edge, in_version, out_version = heapq.heappop(heap)
            if (
                in_version != state.edges[in_edge]['version']
                or out_version != state.edges[out_edge]['version']
            ):
                capacity, refreshed_day = state.pair_common_day_capacity(in_edge, out_edge)
                if capacity > 0:
                    heapq.heappush(
                        heap,
                        (
                            -capacity,
                            refreshed_day,
                            payer,
                            payee,
                            in_edge,
                            out_edge,
                            state.edges[in_edge]['version'],
                            state.edges[out_edge]['version'],
                        ),
                    )
                continue
            capacity = -neg_capacity
            refreshed, refreshed_day = state.pair_common_day_capacity(in_edge, out_edge)
            if refreshed != capacity or refreshed_day != day:
                if refreshed > 0:
                    heapq.heappush(
                        heap,
                        (
                            -refreshed,
                            refreshed_day,
                            payer,
                            payee,
                            in_edge,
                            out_edge,
                            state.edges[in_edge]['version'],
                            state.edges[out_edge]['version'],
                        ),
                    )
                continue
            in_fragments = state.consume(in_edge, day, capacity)
            out_fragments = state.consume(out_edge, day, capacity)
            operations += 1
            compression += 2 * capacity
            bilateral = payer == payee
            if bilateral:
                pmr += 2 * capacity
            else:
                pmr += capacity
                instruction_mass += capacity
                i = j = 0
                rem_i = in_fragments[0][1]
                rem_j = out_fragments[0][1]
                while i < len(in_fragments) and j < len(out_fragments):
                    paired = min(rem_i, rem_j)
                    acceleration = max(0, in_fragments[i][5] - out_fragments[j][5])
                    acceleration_numerator += paired * acceleration
                    if acceleration > 0:
                        positive_acceleration_numerator += paired * acceleration
                        positive_acceleration_mass += paired
                    rem_i -= paired
                    rem_j -= paired
                    if rem_i == 0:
                        i += 1
                        rem_i = in_fragments[i][1] if i < len(in_fragments) else 0
                    if rem_j == 0:
                        j += 1
                        rem_j = out_fragments[j][1] if j < len(out_fragments) else 0
            if keep_logs:
                logs.append(
                    {
                        'kind': 'path',
                        'day': day,
                        'amount': capacity,
                        'payer': payer,
                        'intermediary': intermediary,
                        'payee': payee,
                        'in_edge': in_edge,
                        'out_edge': out_edge,
                        'bilateral': bilateral,
                        'in_fragments': in_fragments,
                        'out_fragments': out_fragments,
                    }
                )

    return {
        'method': 'path_offline',
        'operations': operations,
        'pmr': pmr,
        'compression': compression,
        'instruction_mass': instruction_mass,
        'residual_mass': state.residual_mass(),
        'runtime_seconds': time.time() - start_time,
        'logs': logs,
        'mean_acceleration_days': acceleration_numerator / instruction_mass if instruction_mass else 0.0,
        'positive_only_mean_days': (
            positive_acceleration_numerator / positive_acceleration_mass
            if positive_acceleration_mass
            else 0.0
        ),
        'accelerated_mass_share': (
            positive_acceleration_mass / instruction_mass if instruction_mass else 0.0
        ),
    }


def cycle_offline(base: State, cycles: Sequence[Tuple[Tuple[int, ...], Tuple[int, ...]]], keep_logs: bool = True) -> Dict[str, Any]:
    state = base.copy()
    start_time = time.time()
    heap: List[Tuple[Any, ...]] = []
    for cycle_index, (nodes, edge_ids) in enumerate(cycles):
        capacity, day = state.cycle_common_day_capacity(edge_ids)
        if capacity <= 0:
            continue
        versions = tuple(state.edges[edge_id]['version'] for edge_id in edge_ids)
        heapq.heappush(
            heap,
            (-len(nodes) * capacity, len(nodes), nodes, day, cycle_index, capacity, versions),
        )
    operations = 0
    pmr = 0
    logs: List[Dict[str, Any]] = []
    while heap:
        _, length, nodes, day, cycle_index, capacity, versions = heapq.heappop(heap)
        edge_ids = cycles[cycle_index][1]
        current_versions = tuple(state.edges[edge_id]['version'] for edge_id in edge_ids)
        if current_versions != versions:
            refreshed, refreshed_day = state.cycle_common_day_capacity(edge_ids)
            if refreshed > 0:
                heapq.heappush(
                    heap,
                    (
                        -length * refreshed,
                        length,
                        nodes,
                        refreshed_day,
                        cycle_index,
                        refreshed,
                        current_versions,
                    ),
                )
            continue
        refreshed, refreshed_day = state.cycle_common_day_capacity(edge_ids)
        if refreshed != capacity or refreshed_day != day:
            if refreshed > 0:
                heapq.heappush(
                    heap,
                    (
                        -length * refreshed,
                        length,
                        nodes,
                        refreshed_day,
                        cycle_index,
                        refreshed,
                        current_versions,
                    ),
                )
            continue
        operation_fragments = []
        for edge_id in edge_ids:
            operation_fragments.append((edge_id, state.consume(edge_id, day, capacity)))
        operations += 1
        pmr += length * capacity
        if keep_logs:
            logs.append(
                {
                    'kind': 'cycle',
                    'day': day,
                    'amount': capacity,
                    'nodes': nodes,
                    'edges': edge_ids,
                    'edge_fragments': operation_fragments,
                }
            )
    return {
        'method': 'cycle_offline',
        'operations': operations,
        'pmr': pmr,
        'compression': pmr,
        'instruction_mass': 0,
        'residual_mass': state.residual_mass(),
        'runtime_seconds': time.time() - start_time,
        'logs': logs,
    }


def topology_metrics(records: Sequence[AtomicRecord]) -> Dict[str, Any]:
    graph = nx.DiGraph()
    edge_weight: Dict[Tuple[str, str], int] = defaultdict(int)
    for r in records:
        edge_weight[(r.debtor, r.creditor)] += r.amount
    graph.add_edges_from(edge_weight)
    nodes = graph.number_of_nodes()
    edges = graph.number_of_edges()
    p2 = sum(graph.in_degree(v) * graph.out_degree(v) for v in graph.nodes)
    sccs = list(nx.strongly_connected_components(graph))
    cyclic_nodes = set().union(*(component for component in sccs if len(component) > 1)) if sccs else set()
    cyclic_p2 = sum(graph.in_degree(v) * graph.out_degree(v) for v in cyclic_nodes)
    wccs = list(nx.weakly_connected_components(graph))
    reciprocated = sum(1 for u, v in graph.edges if graph.has_edge(v, u))
    return {
        'firms': nodes,
        'edges': edges,
        'directed_density': nx.density(graph),
        'reciprocity': reciprocated / edges if edges else 0.0,
        'weak_components': len(wccs),
        'largest_wcc_share': max((len(c) for c in wccs), default=0) / nodes if nodes else 0.0,
        'strong_components': len(sccs),
        'largest_scc_share': max((len(c) for c in sccs), default=0) / nodes if nodes else 0.0,
        'firms_nontrivial_scc': len(cyclic_nodes),
        'local_two_edge_paths': p2,
        'cyclic_local_two_edge_paths': cyclic_p2,
        'cycle_closure_ratio': cyclic_p2 / p2 if p2 else 0.0,
        'topological_opportunity_ratio': p2 / cyclic_p2 if cyclic_p2 else math.inf,
    }


def replay_validate(records: Sequence[AtomicRecord], base: State, result: Dict[str, Any]) -> Dict[str, Any]:
    remaining = {r.uid: r.amount for r in records}
    by_uid = {r.uid: r for r in records}
    initial_net: Dict[int, int] = defaultdict(int)
    for r in records:
        debtor = base.node_to_int[r.debtor]
        creditor = base.node_to_int[r.creditor]
        initial_net[creditor] += r.amount
        initial_net[debtor] -= r.amount

    instructions_in: Dict[int, int] = defaultdict(int)
    instructions_out: Dict[int, int] = defaultdict(int)
    temporal_violations = 0
    bad_edge_fragments = 0
    bad_sums = 0
    overconsumed = 0
    reconstructed_pmr = 0
    reconstructed_instruction = 0
    fragment_count = 0

    def apply_fragments(edge_id: int, fragments: Sequence[Tuple[str, int, int, int, int, int]], expected: int, day: int) -> None:
        nonlocal temporal_violations, bad_edge_fragments, bad_sums, overconsumed, fragment_count
        edge = base.edges[edge_id]
        total = 0
        for uid, amount, issue_idx, due_idx, _, _ in fragments:
            fragment_count += 1
            record = by_uid.get(uid)
            if record is None:
                bad_edge_fragments += 1
                continue
            if base.node_to_int[record.debtor] != edge['u'] or base.node_to_int[record.creditor] != edge['v']:
                bad_edge_fragments += 1
            if not (record.issue_idx <= day <= record.due_idx):
                temporal_violations += 1
            if amount <= 0 or remaining[uid] < amount:
                overconsumed += 1
            remaining[uid] -= amount
            total += amount
        if total != expected:
            bad_sums += 1

    for operation in result['logs']:
        amount = operation['amount']
        day = operation['day']
        if operation['kind'] == 'cycle':
            for edge_id, fragments in operation['edge_fragments']:
                apply_fragments(edge_id, fragments, amount, day)
            reconstructed_pmr += len(operation['edges']) * amount
        else:
            apply_fragments(operation['in_edge'], operation['in_fragments'], amount, day)
            apply_fragments(operation['out_edge'], operation['out_fragments'], amount, day)
            if operation['bilateral']:
                reconstructed_pmr += 2 * amount
            else:
                reconstructed_pmr += amount
                reconstructed_instruction += amount
                payer = operation['payer']
                payee = operation['payee']
                instructions_out[payer] += amount
                instructions_in[payee] += amount

    residual_mass = sum(remaining.values())
    terminal_net: Dict[int, int] = defaultdict(int)
    for uid, residual in remaining.items():
        record = by_uid[uid]
        debtor = base.node_to_int[record.debtor]
        creditor = base.node_to_int[record.creditor]
        terminal_net[creditor] += residual
        terminal_net[debtor] -= residual
    for node, value in instructions_in.items():
        terminal_net[node] += value
    for node, value in instructions_out.items():
        terminal_net[node] -= value
    all_nodes = set(initial_net).union(terminal_net)
    max_net_error = max((abs(initial_net[node] - terminal_net[node]) for node in all_nodes), default=0)
    negative_residuals = sum(1 for value in remaining.values() if value < 0)
    post_settlement = residual_mass + reconstructed_instruction
    identity_pmr = base.initial_mass - post_settlement

    checks = {
        'fragment_count': fragment_count,
        'temporal_violations': temporal_violations,
        'bad_edge_fragments': bad_edge_fragments,
        'bad_sums': bad_sums,
        'overconsumed_fragments': overconsumed,
        'negative_residuals': negative_residuals,
        'max_net_error_cents': max_net_error,
        'reconstructed_pmr': reconstructed_pmr,
        'reported_pmr': result['pmr'],
        'pmr_identity_value': identity_pmr,
        'reconstructed_instruction_mass': reconstructed_instruction,
        'reported_instruction_mass': result['instruction_mass'],
        'residual_mass': residual_mass,
        'reported_residual_mass': result['residual_mass'],
    }
    checks['all_pass'] = all(
        [
            temporal_violations == 0,
            bad_edge_fragments == 0,
            bad_sums == 0,
            overconsumed == 0,
            negative_residuals == 0,
            max_net_error == 0,
            reconstructed_pmr == result['pmr'],
            identity_pmr == result['pmr'],
            reconstructed_instruction == result['instruction_mass'],
            residual_mass == result['residual_mass'],
        ]
    )
    return checks


def serializable_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in result.items() if k not in ('logs', 'curve')}


def save_curve(path: Path, cycle_curve: Sequence[int], path_curve: Sequence[int]) -> None:
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['date', 'cycle_pmr_source_units', 'path_pmr_source_units', 'advantage_source_units'])
        for day, (cycle_value, path_value) in enumerate(zip(cycle_curve, path_curve)):
            current = START_2022 + timedelta(days=day)
            writer.writerow([current.isoformat(), cycle_value / 100, path_value / 100, (path_value - cycle_value) / 100])


def run_variant(
    output_dir: Path,
    name: str,
    segment: str,
    linkage: str,
    validated_only: bool,
    deduplicate: bool,
    run_offline: bool,
) -> Dict[str, Any]:
    print(f'Loading {name}...', flush=True)
    records, load_audit = load_2022_dataset(segment, linkage, validated_only, deduplicate)
    base = State(records)
    topology = topology_metrics(records)
    print(
        f"{name}: {len(records):,} records, {base.initial_mass/1e11:.6f} bn source units, "
        f"{len(base.edges):,} edges",
        flush=True,
    )
    enum_start = time.time()
    cycles = enumerate_cycles(base, 8)
    enum_seconds = time.time() - enum_start
    cycle_inventory = Counter(len(nodes) for nodes, _ in cycles)
    print(f'{name}: {len(cycles):,} cycles through length 8 in {enum_seconds:.3f}s', flush=True)

    daily_cycle = cycle_daily(base, cycles, keep_logs=True)
    print(f'{name}: daily cycle done', serializable_summary(daily_cycle), flush=True)
    daily_path = path_daily(base, keep_logs=True)
    print(f'{name}: daily path done', serializable_summary(daily_path), flush=True)
    validation_cycle = replay_validate(records, base, daily_cycle)
    validation_path = replay_validate(records, base, daily_path)
    print(f'{name}: validation cycle={validation_cycle["all_pass"]} path={validation_path["all_pass"]}', flush=True)

    auc = sum(p - c for c, p in zip(daily_cycle['curve'], daily_path['curve']))
    daily_summary = {
        'cycle': serializable_summary(daily_cycle),
        'path': serializable_summary(daily_path),
        'advantage': daily_path['pmr'] - daily_cycle['pmr'],
        'advantage_percentage_points': 100 * (daily_path['pmr'] - daily_cycle['pmr']) / base.initial_mass,
        'relative_advantage_percent': 100 * (daily_path['pmr'] - daily_cycle['pmr']) / daily_cycle['pmr'] if daily_cycle['pmr'] else None,
        'auc_source_unit_days': auc / 100,
        'average_daily_advantage_source_units': auc / (100 * base.horizon),
        'path_above_cycle_days': sum(1 for c, p in zip(daily_cycle['curve'], daily_path['curve']) if p > c),
        'path_below_cycle_days': sum(1 for c, p in zip(daily_cycle['curve'], daily_path['curve']) if p < c),
        'validation_cycle': validation_cycle,
        'validation_path': validation_path,
    }
    save_curve(output_dir / f'{name}_daily_curves.csv', daily_cycle['curve'], daily_path['curve'])

    offline_summary = None
    if run_offline:
        offline_cycle = cycle_offline(base, cycles, keep_logs=True)
        print(f'{name}: offline cycle done', serializable_summary(offline_cycle), flush=True)
        offline_path = path_offline(base, keep_logs=True)
        print(f'{name}: offline path done', serializable_summary(offline_path), flush=True)
        validation_cycle_offline = replay_validate(records, base, offline_cycle)
        validation_path_offline = replay_validate(records, base, offline_path)
        offline_summary = {
            'cycle': serializable_summary(offline_cycle),
            'path': serializable_summary(offline_path),
            'advantage': offline_path['pmr'] - offline_cycle['pmr'],
            'advantage_percentage_points': 100 * (offline_path['pmr'] - offline_cycle['pmr']) / base.initial_mass,
            'relative_advantage_percent': 100 * (offline_path['pmr'] - offline_cycle['pmr']) / offline_cycle['pmr'] if offline_cycle['pmr'] else None,
            'validation_cycle': validation_cycle_offline,
            'validation_path': validation_path_offline,
        }

    summary = {
        'name': name,
        'load_audit': load_audit,
        'initial_mass': base.initial_mass,
        'topology': topology,
        'cycle_count_total': len(cycles),
        'cycle_inventory': dict(sorted(cycle_inventory.items())),
        'cycle_enumeration_seconds': enum_seconds,
        'daily': daily_summary,
        'offline': offline_summary,
    }
    with (output_dir / f'{name}_summary.json').open('w', encoding='utf-8') as handle:
        json.dump(summary, handle, indent=2)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=PACKAGE_ROOT/'new_results')
    parser.add_argument('--variant', choices=['main', 'source_stratified', 'validated', 'dedup', 'h1', 'h2', 'all'], default='main')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    variants = {
        'main': dict(name='full_2022_verified', segment='full', linkage='verified', validated_only=False, deduplicate=False),
        'source_stratified': dict(name='full_2022_source_stratified', segment='full', linkage='none', validated_only=False, deduplicate=False),
        'validated': dict(name='full_2022_validated', segment='full', linkage='verified', validated_only=True, deduplicate=False),
        'dedup': dict(name='full_2022_deduplicated', segment='full', linkage='verified', validated_only=False, deduplicate=True),
        'h1': dict(name='h1_2022', segment='h1', linkage='none', validated_only=False, deduplicate=False),
        'h2': dict(name='h2_2022', segment='h2', linkage='none', validated_only=False, deduplicate=False),
    }
    selected = list(variants.values()) if args.variant == 'all' else [variants[args.variant]]
    summaries = []
    for config in selected:
        summaries.append(run_variant(args.out, run_offline=args.offline, **config))
    with (args.out / 'run_index.json').open('w', encoding='utf-8') as handle:
        json.dump([{'name': s['name'], 'summary_file': f"{s['name']}_summary.json"} for s in summaries], handle, indent=2)


if __name__ == '__main__':
    main()
