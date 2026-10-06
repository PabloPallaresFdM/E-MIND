"""Source metadata contract and deterministic, standard-library-only I/O.

The canonical .yaml uses JSON syntax, a YAML 1.2 subset. Coordinator input may
also use the documented flat YAML subset; this is not a general YAML parser.
"""
import csv
import io
import json
from pathlib import Path
import re
import textwrap
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / 'metadata/source_registry_v0.1.yaml'
DEFAULT_CSV = ROOT / 'metadata/source_registry_v0.1.csv'
DEFAULT_MATRIX = ROOT / 'metadata/country_source_matrix_v0.1.csv'
FIELDS = (
    'source_id', 'domain', 'country_or_region', 'provider', 'dataset_name',
    'dataset_version', 'variable_name', 'variable_description', 'native_unit',
    'native_frequency', 'native_timezone', 'coverage_start', 'coverage_end',
    'access_method', 'api_or_download_endpoint', 'licence_name', 'licence_url',
    'redistribution_allowed', 'commercial_reuse_allowed', 'derivative_reuse_allowed',
    'attribution_required', 'doi', 'citation', 'source_type', 'status', 'reason',
    'known_gaps', 'notes', 'retrieval_verified', 'metadata_checked_at_utc',
)
SOURCE_TYPES = ('observed', 'modelled', 'reanalysis', 'derived', 'benchmark_assumption')
STATUSES = ('ACCEPTED_CORE', 'ACCEPTED_EXTENSION', 'USER_SUPPLIED_ONLY',
            'BLOCKED', 'REJECTED', 'PENDING_REVIEW')
BOOLEAN_FIELDS = frozenset(('redistribution_allowed', 'commercial_reuse_allowed',
                            'derivative_reuse_allowed', 'attribution_required', 'retrieval_verified'))
MATRIX_FIELDS = ('country_or_region', 'domain', 'variable_name', 'source_id',
                 'market_protocol', 'status', 'reason', 'notes')
MARKET_PROTOCOLS = ('M1_LOCAL_OPEN', 'M2_COMMON_OPEN_SIGNAL', 'M3_USER_SUPPLIED_LOCAL')
SENSITIVE_KEY = re.compile(r'(?i)(?:token|secret|password|passwd|credential|authorization|cookie|api[_-]?key|access[_-]?key|private[_-]?key|signature|^sig$|^key$)')


class RegistryError(ValueError):
    """Invalid metadata, with errors that never echo potentially secret values."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError('Duplicate mapping key in input')
        result[key] = value
    return result


def json_load(text):
    return json.loads(text, object_pairs_hook=unique_object)


def _scalar(value):
    """Decode scalar input without YAML implicit dates/numbers or object loading."""
    value = value.strip()
    if value.startswith('#'):
        return None
    if value.startswith('"'):
        decoder = json.JSONDecoder()
        try:
            parsed, end = decoder.raw_decode(value)
        except ValueError:
            raise RegistryError('Invalid double-quoted scalar') from None
        trailing = value[end:].strip()
        if not isinstance(parsed, str) or (trailing and not trailing.startswith('#')):
            raise RegistryError('Unexpected text after quoted scalar')
        return parsed
    if value.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", value)
        if not match:
            raise RegistryError('Invalid single-quoted scalar')
        return match.group(1).replace("''", "'")
    value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
    if not value or value.lower() in ('null', '~'):
        return None
    if value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    if value.startswith(('!', '&', '*', '[', '{', '|', '>')) or re.search(r':\s', value):
        raise RegistryError('Unsupported YAML scalar; quote text or use JSON')
    return value


def read_entry(path):
    text = Path(path).read_text(encoding='utf-8')
    if text.lstrip().startswith(('{', '[')):
        try:
            entry = json_load(text)
        except json.JSONDecodeError:
            raise RegistryError('Invalid JSON entry') from None
    else:
        # Flat mappings only. No tags, anchors, aliases, nested mappings or lists.
        entry, index = {}, 0
        lines = text.splitlines()
        document_started = False
        while index < len(lines):
            line = lines[index]
            index += 1
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            if line == '---' and not document_started and not entry:
                document_started = True
                continue
            match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*))?', line)
            if not match:
                raise RegistryError('Expected a flat YAML key: scalar mapping; use JSON for other syntax')
            key, value = match.group(1), match.group(2) or ''
            if key in entry:
                raise RegistryError('Duplicate mapping key in YAML input')
            style = re.split(r'\s+#', value, maxsplit=1)[0].strip()
            if style in ('|', '|-', '>', '>-'):
                block = []
                while index < len(lines) and (not lines[index].strip() or lines[index].startswith(' ')):
                    block.append(lines[index])
                    index += 1
                content = textwrap.dedent('\n'.join(block)).rstrip('\n')
                if style.startswith('>'):
                    # Restrict folding to ordinary paragraphs; complex indentation
                    # is rejected so scientific text is not silently reinterpreted.
                    if any(x.startswith(' ') for x in content.splitlines()):
                        raise RegistryError('Complex folded YAML text is unsupported; use a JSON string')
                    # One break becomes a space; a run of blank lines retains
                    # one fewer break (the ordinary YAML paragraph folding rule).
                    content = re.sub(r'\n+', lambda match: ' ' if len(match.group()) == 1
                                     else '\n' * (len(match.group()) - 1), content)
                entry[key] = content + ('\n' if content and not style.endswith('-') else '')
            else:
                entry[key] = _scalar(value)
    if not isinstance(entry, dict):
        raise RegistryError('An entry must be one mapping, not a list or registry document')
    return entry


def check_no_secrets(value):
    """Reject secret-bearing keys and credential-bearing URLs/assignments.

    No finite pattern scan guarantees that arbitrary free text is secret-free;
    the guide also requires coordinator/reviewer inspection.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or SENSITIVE_KEY.search(key):
                raise RegistryError('Credential/token fields are forbidden')
            check_no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            check_no_secrets(item)
    elif isinstance(value, str):
        if re.search(r'-----BEGIN [A-Z ]*PRIVATE KEY-----', value):
            raise RegistryError('Private-key material is forbidden')
        if re.search(r'(?i)\b(?:api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+', value):
            raise RegistryError('Credential-like assignment is forbidden')
        for url in re.findall(r'https?://[^\s<>\"]+', value):
            try:
                parts = urlsplit(url)
                if parts.username is not None or parts.password is not None:
                    raise RegistryError('URL user information is forbidden')
                pairs = parse_qsl(parts.query, keep_blank_values=True) + parse_qsl(parts.fragment, keep_blank_values=True)
                if any(SENSITIVE_KEY.search(key) for key, _ in pairs):
                    raise RegistryError('Credential-bearing URLs are forbidden; use a public endpoint')
            except ValueError:
                raise RegistryError('Invalid or credential-bearing URL') from None


def validate_sources(sources):
    if not isinstance(sources, list):
        raise RegistryError('sources must be a list')
    seen = set()
    for index, entry in enumerate(sources, 1):
        if not isinstance(entry, dict):
            raise RegistryError('Each source must be a mapping')
        check_no_secrets(entry)
        missing, extra = set(FIELDS) - set(entry), set(entry) - set(FIELDS)
        if missing:
            raise RegistryError('Source {} missing required fields: {}'.format(index, ', '.join(sorted(missing))))
        if extra:
            raise RegistryError('Source {} has unsupported fields'.format(index))
        for key, value in entry.items():
            if key in BOOLEAN_FIELDS:
                if value is not None and type(value) is not bool:
                    raise RegistryError('Source {}: {} must be true, false or null'.format(index, key))
            elif value is not None and not isinstance(value, str):
                raise RegistryError('Source {}: {} must be text or null'.format(index, key))
        source_id = entry['source_id']
        if not source_id or source_id != source_id.strip():
            raise RegistryError('source_id must be nonempty text without surrounding whitespace')
        if source_id in seen:
            raise RegistryError('Duplicate source_id')
        seen.add(source_id)
        if entry['source_type'] not in SOURCE_TYPES:
            raise RegistryError('Invalid source_type')
        if entry['status'] not in STATUSES:
            raise RegistryError('Invalid status')
        required = []
        if entry['status'] in ('ACCEPTED_CORE', 'ACCEPTED_EXTENSION'):
            required.extend(('provider', 'dataset_name', 'licence_url', 'access_method'))
        if entry['status'] == 'BLOCKED':
            required.append('reason')
        for key in required:
            if not entry[key] or not entry[key].strip():
                raise RegistryError('Source {}: {} requires nonempty {}'.format(index, entry['status'], key))
        stamp = entry['metadata_checked_at_utc']
        if stamp:
            from datetime import datetime, timedelta
            try:
                parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                if parsed.utcoffset() != timedelta(0):
                    raise ValueError()
            except ValueError:
                raise RegistryError('metadata_checked_at_utc must be an ISO 8601 UTC timestamp') from None


def document(sources):
    validate_sources(sources)
    ordered = [{key: entry[key] for key in FIELDS} for entry in sorted(sources, key=lambda x: x['source_id'])]
    return {'schema_version': '0.1', 'fields': list(FIELDS),
            'source_type_values': list(SOURCE_TYPES), 'status_values': list(STATUSES), 'sources': ordered}


def read_registry(path):
    try:
        data = json_load(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        raise RegistryError('Canonical registry must use the documented JSON-compatible YAML format') from None
    if not isinstance(data, dict) or set(data) != {'schema_version', 'fields', 'source_type_values', 'status_values', 'sources'}:
        raise RegistryError('Invalid registry schema/header')
    expected = document([])
    for key in ('schema_version', 'fields', 'source_type_values', 'status_values'):
        if data[key] != expected[key]:
            raise RegistryError('Registry schema/header differs from version 0.1')
    validate_sources(data['sources'])
    return data['sources']


def yaml_text(sources):
    return json.dumps(document(sources), ensure_ascii=False, indent=2) + '\n'


def csv_text(sources):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    for entry in document(sources)['sources']:
        writer.writerow({key: ('true' if value is True else 'false' if value is False else '' if value is None else value)
                         for key, value in entry.items()})
    return stream.getvalue()


def checked_rows(reader):
    try:
        yield from reader
    except csv.Error:
        raise RegistryError("Malformed CSV quoting") from None


def read_csv(path):
    with Path(path).open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != list(FIELDS):
            raise RegistryError('CSV header must match the ordered version 0.1 fields')
        entries = []
        for row in checked_rows(reader):
            if None in row or any(value is None for value in row.values()):
                raise RegistryError('CSV row has incorrect field count')
            for key, value in row.items():
                if value == '':
                    row[key] = None
                elif key in BOOLEAN_FIELDS:
                    if value not in ('true', 'false'):
                        raise RegistryError('CSV boolean must be true, false or empty')
                    row[key] = value == 'true'
            entries.append(row)
    validate_sources(entries)
    return entries


def validate_pair(registry, csv_path):
    sources, csv_sources = read_registry(registry), read_csv(csv_path)
    if csv_text(sources) != csv_text(csv_sources):
        raise RegistryError('YAML and CSV entries disagree')
    return sources


def validate_matrix(path, sources):
    source_ids = {entry['source_id'] for entry in sources}
    with Path(path).open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != list(MATRIX_FIELDS):
            raise RegistryError('Country matrix header differs from the documented schema')
        seen = set()
        for row in checked_rows(reader):
            if None in row or any(value is None for value in row.values()):
                raise RegistryError('Country matrix row has incorrect field count')
            check_no_secrets(row)
            if not row['country_or_region'].strip() or row['source_id'] not in source_ids:
                raise RegistryError('Matrix requires country_or_region and a registered source_id')
            if row['market_protocol'] and row['market_protocol'] not in MARKET_PROTOCOLS:
                raise RegistryError('Invalid matrix market_protocol')
            if row['status'] not in STATUSES:
                raise RegistryError('Invalid matrix status')
            if row['status'] == 'BLOCKED' and not row['reason'].strip():
                raise RegistryError('BLOCKED matrix row requires reason')
            key = tuple(row[field] for field in MATRIX_FIELDS[:5])
            if key in seen:
                raise RegistryError('Duplicate country/source matrix mapping')
            seen.add(key)
    return len(seen)
