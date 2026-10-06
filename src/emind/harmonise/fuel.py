"""Native weekly Germany diesel audit, adapted from interrupted Phase03B code.

Reads the two reviewed XLSX fields without rewriting the workbook. This is an
interim audit representation, NOT a canonical fuel selection or currency claim.
No hourly expansion, interpolation, tax choice or publication-time guess occurs.
"""
from collections import Counter
from datetime import timedelta, date
from decimal import Decimal, InvalidOperation
import posixpath
import xml.etree.ElementTree as ET
import zipfile

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
VARIANTS = (
    ('Prices with taxes', 'WITH_TAX', 'DE_price_with_tax_diesel'),
    ('Prices wo taxes', 'WITHOUT_TAX', 'DE_price_wo_tax_diesel'),
)


def excel_date(value, date1904=False):
    serial = Decimal(value)
    if not serial.is_finite() or serial != serial.to_integral_value():
        raise ValueError('Expected a whole-day Excel date serial')
    if not date1904 and serial == 60:
        raise ValueError('Excel fictional 1900-02-29 is not a civil date')
    base = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
    if not date1904 and serial < 60:
        base = date(1899, 12, 31)
    return base + timedelta(days=int(serial))


def _cell(cell, shared):
    if cell.find('s:f', NS) is not None:
        raise ValueError('Formula cells require an explicit provider audit')
    kind = cell.get('t', 'n')
    value = cell.find('s:v', NS)
    text = value.text if value is not None else None
    if kind == 's':
        if text is None or not text.isdigit() or int(text) >= len(shared):
            raise ValueError('Invalid shared string reference')
        return shared[int(text)], 'text'
    if kind == 'inlineStr':
        return ''.join(x.text or '' for x in cell.findall('s:is//s:t', NS)), 'text'
    if kind != 'n':
        raise ValueError('Unsupported workbook cell type: ' + kind)
    return text, 'numeric'


def extract_germany(path, start=date(2019, 1, 1), end=date(2023, 12, 31)):
    """Return weekly rows for BOTH tax bases and audit evidence; never fill gaps.

    Schema support is intentionally limited to Date in A and the exact Germany
    diesel field/unit headers in BD. A blank numeric cell is MISSING; strings,
    errors, booleans and cached formula results are not silently treated as data.
    Caller verifies the immutable workbook hash before and after this read.
    """
    if start > end:
        raise ValueError('Invalid fuel date range')
    rows, evidence = [], {}
    with zipfile.ZipFile(path) as book:
        if book.testzip():
            raise ValueError('Corrupt workbook')
        shared = []
        if 'xl/sharedStrings.xml' in book.namelist():
            shared = [''.join(t.text or '' for t in x.findall('.//s:t', NS))
                      for x in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        workbook = ET.fromstring(book.read('xl/workbook.xml'))
        properties = workbook.find('s:workbookPr', NS)
        date1904 = properties is not None and properties.get('date1904') in ('1', 'true')
        rels = {r.get('Id'): r for r in ET.fromstring(book.read('xl/_rels/workbook.xml.rels'))}
        sheet_elements = workbook.findall('s:sheets/s:sheet', NS)
        sheets = {s.get('name'): s for s in sheet_elements}
        if len(sheets) != len(sheet_elements):
            raise ValueError('Duplicate workbook sheet names')
        for sheet, basis, expected in VARIANTS:
            if sheet not in sheets or sheets[sheet].get(REL) not in rels:
                raise ValueError('Missing required diesel sheet: ' + sheet)
            relationship = rels[sheets[sheet].get(REL)]
            if relationship.get('TargetMode') == 'External':
                raise ValueError('External worksheet relationship is unsupported')
            target = relationship.get('Target', '')
            member = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
            if not member.startswith('xl/') or member not in book.namelist():
                raise ValueError('Unsupported worksheet target')
            header, selected, previous_index = {}, [], 0
            notes_footer_row = None
            with book.open(member) as stream:
                for _, element in ET.iterparse(stream, events=('end',)):
                    if element.tag != '{' + NS['s'] + '}row':
                        continue
                    index = int(element.get('r'))
                    if index <= previous_index:
                        raise ValueError('Duplicate/unordered worksheet rows')
                    previous_index = index
                    cells = {}
                    for cell in element.findall('s:c', NS):
                        ref = cell.get('r', '')
                        column = ref.rstrip('0123456789')
                        if column not in ('A', 'BD'):
                            continue
                        if ref != column + str(index) or column in cells:
                            raise ValueError('Invalid/duplicate selected cell reference')
                        cells[column] = _cell(cell, shared)
                    if index <= 3:
                        header[index] = {k: v[0] for k, v in cells.items()}
                        element.clear()
                        continue
                    date_value, date_kind = cells.get('A', (None, 'numeric'))
                    raw, kind = cells.get('BD', (None, 'numeric'))
                    if date_value is None:
                        if raw is not None:
                            raise ValueError('Diesel value without a date')
                        element.clear()
                        continue
                    if date_kind != 'numeric':
                        if date_value == 'Notes:' and raw is None and notes_footer_row is None:
                            notes_footer_row = index
                            element.clear()
                            continue
                        raise ValueError('Unsupported workbook date representation')
                    if notes_footer_row is not None:
                        raise ValueError('Unexpected observation after Notes footer')
                    day = excel_date(date_value, date1904)
                    if start <= day <= end:
                        if kind != 'numeric':
                            raise ValueError('Unsupported diesel numeric representation')
                        try:
                            value = Decimal(raw) if raw is not None else None
                        except InvalidOperation as exc:
                            raise ValueError('Invalid diesel numeric value') from exc
                        if value is not None and not value.is_finite():
                            raise ValueError('Invalid diesel numeric value')
                        selected.append({
                            'date': day.isoformat(), 'date_serial_raw': date_value,
                            'sheet_name': sheet, 'worksheet_member': member,
                            'source_row_index': index, 'date_cell': 'A' + str(index),
                            'value_cell': 'BD' + str(index), 'source_field': expected,
                            'tax_basis': basis, 'diesel_price_native': value,
                            'value_raw': raw, 'native_unit_denominator': '1000 l',
                            'native_currency': 'UNKNOWN_IN_WORKBOOK',
                            'source_native_frequency': 'weekly',
                            'source_id': 'eu_ec_weekly_oil_bulletin_diesel',
                            'quality_flag': 'ORIGINAL' if value is not None else 'MISSING',
                        })
                    element.clear()
            if (header.get(1, {}).get('BD') != expected
                    or header.get(3, {}).get('A') != 'Date'
                    or header.get(3, {}).get('BD') != '1000 l'):
                raise ValueError('Unreviewed Germany diesel/date/unit header')
            selected.sort(key=lambda r: (r['date'], r['source_row_index']))
            dates = [date.fromisoformat(r['date']) for r in selected]
            if not dates:
                raise ValueError('No Germany diesel dates in target period')
            counts = Counter(dates)
            evidence[basis] = {
                'sheet': sheet, 'worksheet_member': member, 'header': header,
                'excel_date1904': date1904, 'row_count': len(selected),
                'notes_footer_row': notes_footer_row,
                'missing_values': sum(r['diesel_price_native'] is None for r in selected),
                'duplicate_dates': {d.isoformat(): n for d, n in counts.items() if n > 1},
                'first_date': dates[0].isoformat(), 'last_date': dates[-1].isoformat(),
                'weekdays_iso': dict(Counter(d.isoweekday() for d in dates)),
                'annual_counts': dict(Counter(d.year for d in dates)),
                'nonseven_day_gaps': [
                    {'from': a.isoformat(), 'to': b.isoformat(), 'days': (b - a).days}
                    for a, b in zip(dates, dates[1:]) if (b - a).days != 7],
                'unit_denominator': '1000 l', 'currency': None,
                'weekly_convention': 'Workbook Date only; reference/publication role and timezone unknown. No hourly mapping.',
            }
            rows.extend(selected)
    return rows, evidence
