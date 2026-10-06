"""Scenario-neutral offline gated RC/dev packaging; no acquisition or publication API."""
import json
import re
import shutil
import unicodedata
from decimal import Decimal
from pathlib import Path, PurePosixPath

from emind.scenario import fingerprint, sha256
from emind.release_gates import RC_VERSION, STATE, validate_rc_gates

SCHEMA_VERSION = '0.1.0'
IDENTITY_SCHEME = 'emind-release-identity-1'
PUBLIC_SCENARIO_SCHEME = 'emind-public-composition-1'
CANONICAL_RULE = ('UTF-8; NFC strings; sorted keys; compact separators; no trailing LF; '
                  'no NaN/Infinity/duplicate keys; decimal parameters as normalized strings; '
                  'integers retained; unordered arrays explicitly sorted by ID/path')
PENDING = ['source-specific RAW/canonical rights',
           'owner code/metadata license selection', 'formal citation authorship/date',
           'public version and archive DOI', 'cross-platform certification']
SIDE_FIELDS = {'component_id', 'semantic_kind', 'authoritative_path', 'accepted_artifact_sha256',
               'format', 'columns', 'units', 'key_columns', 'ordering', 'spatial_support',
               'native_temporal_resolution', 'valid_time_representation', 'available_at',
               'source_ids', 'snapshot_ids', 'transformation_id', 'missingness_policy'}
FORBIDDEN_FIELDS = {'reward', 'objective_function', 'controller_action_schema',
                    'controller_observation_schema', 'reference_plant_parameters',
                    'soc_trajectory', 'battery_sizing', 'pv_generator_model',
                    'wind_turbine_model', 'diesel_generator_dispatch', 'feasibility_label'}
CONTRACTS = {
    'manifest': {'dataset_name': 'string', 'release_version': 'string', 'release_id': 'string',
                 'generated_by_code_commit': 'string', 'scenarios': 'array', 'files': 'array',
                 'release_fingerprint': 'string', 'release_fingerprint_reference': 'string'},
    'identity': {'release_version': 'string', 'generated_by_code_commit': 'string',
                 'scenarios': 'array', 'release_fingerprint': 'string', 'projection': 'object',
                 'fingerprint_algorithm': 'string', 'canonical_serialization': 'string'},
    'validation': {'status': 'string', 'build_mode': 'string', 'checks': 'object',
                   'known_unresolved_publication_items': 'array'},
    'scenario': {'scenario_id': 'string', 'scenario_class': 'string', 'horizon': 'object',
                 'rows': 'integer', 'components': 'object', 'hourly_artifact': 'string',
                 'scenario_fingerprint': 'string', 'accepted_composition_scheme': 'string',
                 'public_composition_fingerprint': 'string'},
    'metadata': {'scenario_id': 'string', 'hourly_columns': 'array', 'side_components': 'array'},
    'lineage': {'scenario_id': 'string', 'components': 'array', 'assembly_transformation': 'string'},
}


def _normalize(value):
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError('JSON keys must be strings')
            key = unicodedata.normalize('NFC', k)
            if key in result:
                raise ValueError('Duplicate normalized key')
            result[key] = _normalize(v)
        return result
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    if isinstance(value, str):
        return unicodedata.normalize('NFC', value)
    if isinstance(value, (float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError('Non-finite number')
        return '0' if number == 0 else format(number.normalize(), 'f')
    if value is None or isinstance(value, (bool, int)):
        return value
    raise ValueError('Unsupported canonical JSON type')


def canonical_json(value):
    return json.dumps(_normalize(value), sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(value):
    # Reuse accepted SHA256 sorted compact JSON helper, after public normalization.
    return fingerprint(_normalize(value))


def read_json(path):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('Duplicate JSON key')
            result[k] = v
        return result
    def invalid(value):
        raise ValueError('Non-finite JSON number')
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=invalid)


def relative_path(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(ord(c) < 32 or c.isspace() for c in value)
            or unicodedata.normalize('NFC', value) != value
            or value.startswith('/') or any(p in {'', '.', '..'} for p in value.split('/'))
            or str(PurePosixPath(value)) != value):
        raise ValueError('Expected normalized relative POSIX path')
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
        raise ValueError('Invalid ASCII identifier')
    return value


def public_scan(data):
    """Scan bytes, including numeric artifacts; reject local paths and credentials."""
    low = data.lower()
    for marker in [b'/scratch1/', b'/users/', b'fontdemo', b'shirka', b'/home/',
                   b'/tmp/', b'/var/tmp/', b'/private/', b'username', b'hostname', b'file://', b'-----begin private key',
                   b'-----begin openssh private key']:
        if marker in low:
            raise ValueError('Private provenance/secret marker in public payload')
    if re.search(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9]{24,})', data):
        raise ValueError('Secret token pattern in public payload')
    if re.search(rb'(?:[a-z]:[\\/][a-z0-9_. -]{3,}[\\/]|\\\\[a-z][a-z0-9_.-]{2,}\\|(?:api[_-]?key|access[_-]?token|password|authorization|cdsapi_key)\s*["\x27]?\s*[:=]\s*["\x27]?[^\s"\x27,}]+)', low):
        raise ValueError('Credential or platform path in public payload')


def validate_document(kind, document):
    if document.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Unsupported public schema')
    types = {'string': str, 'array': list, 'object': dict, 'integer': int}
    for field, type_name in CONTRACTS[kind].items():
        if type(document.get(field)) is not types[type_name]:
            raise ValueError(f'{kind}: invalid/missing {field}')
    public_scan(canonical_json(document))
    validate_public_values(document)
    if kind == 'metadata':
        for component in document['side_components']:
            if not SIDE_FIELDS <= component.keys():
                raise ValueError('Incomplete generic native side-component contract')
            relative_path(component['authoritative_path'])
            identifier(component['component_id'])


def validate_public_values(value):
    if isinstance(value, dict):
        if FORBIDDEN_FIELDS.intersection(value):
            raise ValueError('Task/plant scientific fields in Data package')
        for field in ['historical_colocated_microgrid', 'co_location_claim',
                      'causal_availability_claim', 'valid_time_is_available_at', 'demand_scaling']:
            if value.get(field) is True:
                raise ValueError('Unsupported scientific/availability claim')
        for v in value.values():
            validate_public_values(v)
    elif isinstance(value, list):
        for v in value:
            validate_public_values(v)
    elif isinstance(value, str):
        if value.startswith('/') or re.match(r'^[A-Za-z]:[\\/]', value) or value.startswith('\\\\'):
            raise ValueError('Absolute metadata path')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value) + b'\n')


def files(root):
    root = Path(root)
    paths = []
    for p in root.rglob('*'):
        if p.is_symlink():
            raise ValueError('Symlinks are forbidden')
        if p.is_file():
            paths.append(relative_path(p.relative_to(root).as_posix()))
    if len({p.casefold() for p in paths}) != len(paths):
        raise ValueError('Case-colliding paths')
    return sorted(paths, key=lambda s: s.encode('utf-8'))


def checksums(root):
    return ''.join(f'{sha256(Path(root) / p)}  {p}\n' for p in files(root)
                   if p != 'checksums.sha256').encode('utf-8')


def _entry(root, path, role, authoritative):
    suffix = Path(path).suffix
    fmt, media = {'.json': ('JSON', 'application/json'), '.parquet': ('Parquet', 'application/vnd.apache.parquet'),
                  '.csv': ('CSV', 'text/csv')}.get(suffix, ('UTF-8 text', 'text/plain'))
    return {'path': path, 'sha256': sha256(root / path), 'size_bytes': (root / path).stat().st_size,
            'role': role, 'authoritative': authoritative, 'convenience': False, 'format': fmt, 'media_type': media}


def build_release(spec, inputs, output, code_commit, implementation_hashes, *, allow_dev=False):
    """Copy inputs indexed by artifact ID. spec has public metadata, never input paths.

    Local gated RCs or explicitly allowed development dry runs. New output only.
    RC publication prose/CFF/licenses join authoritative JSON in the identity.
    """
    output = Path(output)
    if output.exists():
        raise ValueError('Refusing existing output directory')
    version = identifier(spec['release_version'])
    rc = bool(re.fullmatch(RC_VERSION, version))
    if rc:
        state = validate_rc_gates(spec)
    elif allow_dev and re.fullmatch(r'\d+\.\d+\.\d+-dev(?:\.[A-Za-z0-9.-]+)?', version):
        state = {'publication_status': 'PENDING_REVIEW', 'citation_status': 'PENDING', 'doi': None}
    else:
        raise ValueError('Gated RC or explicitly allowed dev version required')
    mode = 'local-rc' if rc else 'dry-run'
    if not re.fullmatch(r'[a-f0-9]{40}', code_commit):
        raise ValueError('Invalid code commit')
    public_scan(canonical_json(spec))
    validate_public_values(spec)
    pending = spec.get('publication_items', PENDING)
    scenario_inputs = sorted(spec['scenarios'], key=lambda x: x['scenario_id'])
    if not scenario_inputs or len({s['scenario_id'] for s in scenario_inputs}) != len(scenario_inputs):
        raise ValueError('Missing/duplicate scenarios')
    # Preflight every scientific identity and artifact before creating output.
    for s in scenario_inputs:
        identifier(s['scenario_id'])
        accepted = read_json(inputs[s['accepted_descriptor_id']])
        if fingerprint(accepted) != s['scenario']['scenario_fingerprint']:
            raise ValueError('Accepted scenario identity mismatch')
        for a in s['artifacts']:
            relative_path(a['path'])
            if Path(inputs[a['artifact_id']]).is_symlink() or sha256(inputs[a['artifact_id']]) != a['sha256']:
                raise ValueError('Accepted artifact hash mismatch')
    output.mkdir(parents=True, exist_ok=False)
    roles = {}
    def save(path, value, role='semantic_metadata', authoritative=True):
        relative_path(path)
        if path in roles:
            raise ValueError('Duplicate package path')
        write_json(output / path, value)
        roles[path] = (role, authoritative)
    for name, value in sorted(spec['metadata'].items()):
        save(f'metadata/{relative_path(name)}', value)
    scenarios = []
    for s in scenario_inputs:
        sid = s['scenario_id']; base = f'scenarios/{sid}'
        for a in sorted(s['artifacts'], key=lambda a: a['path']):
            path = f'{base}/{a["path"]}'
            if path in roles:
                raise ValueError('Duplicate scientific artifact')
            dest = output / path; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(inputs[a['artifact_id']], dest)
            if sha256(dest) != a['sha256']:
                raise ValueError('Byte copy changed scientific artifact')
            roles[path] = (a['role'], True)
        descriptor = dict(s['scenario'], schema_version=SCHEMA_VERSION)
        projection = {k: v for k, v in descriptor.items() if k != 'public_composition_fingerprint'}
        descriptor['public_composition_fingerprint'] = digest(projection)
        descriptor['public_composition_scheme'] = PUBLIC_SCENARIO_SCHEME
        documents = {'scenario': descriptor, 'metadata': dict(s['metadata'], schema_version=SCHEMA_VERSION),
                     'lineage': dict(s['lineage'], schema_version=SCHEMA_VERSION),
                     'validation': {'schema_version': SCHEMA_VERSION, 'status': 'PASS', 'build_mode': mode,
                                    'checks': {'accepted_composition_fingerprint': True, 'byte_preserving_copy': True},
                                    'scenario_fingerprint': descriptor['scenario_fingerprint'],
                                    'known_unresolved_publication_items': pending, **(state if rc else {})}}
        for kind, value in documents.items():
            validate_document(kind, value); save(f'{base}/{kind}.json', value,
                'validation' if kind == 'validation' else 'semantic_metadata', kind != 'validation')
        scenarios.append({'scenario_id': sid, 'scenario_class': descriptor['scenario_class'],
                          'scenario_fingerprint': descriptor['scenario_fingerprint'],
                          'accepted_composition_scheme': descriptor['accepted_composition_scheme'],
                          'public_composition_fingerprint': descriptor['public_composition_fingerprint'],
                          'path': f'{base}/scenario.json'})
        (output / base / 'checksums.sha256').write_bytes(checksums(output / base))
        roles[f'{base}/checksums.sha256'] = ('checksums', False)
    save('metadata/scenario_registry.json', {'schema_version': SCHEMA_VERSION, 'scenarios': scenarios})
    for name, content in spec['text_files'].items():
        relative_path(name)
        if name in roles:
            raise ValueError('Duplicate text file')
        (output / name).write_bytes(content.encode('utf-8')); roles[name] = ('publication_metadata' if rc else 'documentation', rc)
    (output / 'VERSION').write_bytes((version + '\n').encode('utf-8')); roles['VERSION'] = ('version', False)
    authoritative = [_entry(output, p, *roles[p]) for p in sorted(roles) if roles[p][1]]
    projection = {'identity_scheme': IDENTITY_SCHEME, 'schema_version': SCHEMA_VERSION,
                  'release_version': version, 'scenarios': scenarios,
                  'authoritative_files': [{k: a[k] for k in ['path', 'role', 'sha256']} for a in authoritative]}
    release_fp = digest(projection)
    identity = {'schema_version': SCHEMA_VERSION, 'release_version': version, 'doi': None,
                'generated_by_code_commit': code_commit, 'code_state': 'UNCOMMITTED_IMPLEMENTATION',
                'implementation_sha256': implementation_hashes, 'scenarios': scenarios,
                'release_fingerprint': release_fp, 'fingerprint_algorithm': 'SHA-256',
                'canonical_serialization': CANONICAL_RULE, 'projection': projection, **state}
    validate_document('identity', identity); save('identity.json', identity, 'identity', False)
    validation = {'schema_version': SCHEMA_VERSION, 'status': 'PASS', 'build_mode': mode,
                  'checks': {'input_scenario_fingerprint_verification': True, 'file_checksum_verification': True,
                             'no_network_build': True, 'portability_validation': True},
                  **state, 'known_unresolved_publication_items': pending}
    validate_document('validation', validation); save('validation.json', validation, 'validation', False)
    manifest = {'schema_version': SCHEMA_VERSION, 'dataset_name': spec['dataset_name'],
                'release_version': version, 'release_id': f'E-MIND-{version}',
                'generated_by_code_commit': code_commit, 'code_state': 'UNCOMMITTED_IMPLEMENTATION',
                'scenarios': scenarios, 'files': [_entry(output, p, *roles[p]) for p in sorted(roles)],
                'release_fingerprint': release_fp, 'release_fingerprint_reference': 'identity.json',
                **state, 'build_mode': mode}
    validate_document('manifest', manifest); write_json(output / 'manifest.json', manifest)
    (output / 'checksums.sha256').write_bytes(checksums(output))
    return verify_release(output)


def verify_release(root):
    """Read-only integrity, identity, inventory, schema and portability verification."""
    root = Path(root)
    paths = files(root)
    for path in paths:
        public_scan((root / path).read_bytes())
    if (root / 'checksums.sha256').read_bytes() != checksums(root):
        raise ValueError('Root checksum mismatch')
    manifest = read_json(root / 'manifest.json'); identity = read_json(root / 'identity.json')
    for name in ['manifest', 'identity', 'validation']:
        validate_document(name, read_json(root / f'{name}.json'))
    inventory = [e['path'] for e in manifest['files']]
    if inventory != sorted(set(paths) - {'manifest.json', 'checksums.sha256'}):
        raise ValueError('Manifest inventory mismatch')
    for entry in manifest['files']:
        if entry != _entry(root, entry['path'], entry['role'], entry['authoritative']):
            raise ValueError('Manifest file identity mismatch')
    authoritative = [{k: e[k] for k in ['path', 'role', 'sha256']} for e in manifest['files'] if e['authoritative']]
    expected = {'identity_scheme': IDENTITY_SCHEME, 'schema_version': SCHEMA_VERSION,
                'release_version': manifest['release_version'], 'scenarios': manifest['scenarios'],
                'authoritative_files': authoritative}
    if identity['projection'] != expected or digest(expected) != identity['release_fingerprint'] or identity['release_fingerprint'] != manifest['release_fingerprint']:
        raise ValueError('Release identity mismatch')
    version = manifest['release_version']
    if identity['release_version'] != version or (root / 'VERSION').read_text() != version + '\n':
        raise ValueError('Release version mismatch')
    if re.fullmatch(RC_VERSION, version):
        spec = {'release_version': version, 'publication_items': [],
                'metadata': {p.removeprefix('metadata/'): read_json(root / p)
                             for p in paths if p.startswith('metadata/') and p.endswith('.json')},
                'text_files': {name: (root / name).read_text() if (root / name).exists() else ''
                               for name in ('README.md', 'CITATION.cff', 'LICENSE', 'LICENSE-METADATA')},
                'scenarios': [{'scenario': read_json(root / s['path'])} for s in manifest['scenarios']]}
        state = validate_rc_gates(spec)
        for document in (manifest, identity, read_json(root / 'validation.json')):
            if any(document.get(k) != v or k not in document for k, v in state.items()):
                raise ValueError('Inconsistent RC publication metadata')
        validation = read_json(root / 'validation.json')
        if validation['known_unresolved_publication_items'] != []:
            raise ValueError('Stale RC blockers')
    for s in manifest['scenarios']:
        relative_path(s['path']); directory = (root / s['path']).parent
        if (directory / 'checksums.sha256').read_bytes() != checksums(directory):
            raise ValueError('Scenario checksum mismatch')
        for kind in ['scenario', 'metadata', 'lineage', 'validation']:
            validate_document(kind, read_json(directory / f'{kind}.json'))
        if re.fullmatch(RC_VERSION, version):
            scenario_validation = read_json(directory / 'validation.json')
            if (scenario_validation['known_unresolved_publication_items'] != []
                    or any(scenario_validation.get(k) != v for k, v in STATE.items())):
                raise ValueError('Stale scenario RC blockers/state')
        d = read_json(directory / 'scenario.json')
        public_fp = digest({k: v for k, v in d.items() if k not in {'public_composition_fingerprint', 'public_composition_scheme'}})
        if public_fp != d['public_composition_fingerprint'] or public_fp != s['public_composition_fingerprint'] or d['scenario_fingerprint'] != s['scenario_fingerprint']:
            raise ValueError('Scenario composition mismatch')
        meta = read_json(directory / 'metadata.json')
        for side in meta['side_components']:
            path = directory / relative_path(side['authoritative_path'])
            if sha256(path) != side['accepted_artifact_sha256']:
                raise ValueError('Native side-component reference mismatch')
        if (root / 'metadata/source_registry.json').exists():
            sources = read_json(root / 'metadata/source_registry.json')['sources']
            snapshots = read_json(root / 'metadata/snapshot_registry.json')['snapshots']
            source_ids = {x['source_id'] for x in sources}
            snapshot_ids = {x['snapshot_id'] for x in snapshots}
            if len(source_ids) != len(sources) or len(snapshot_ids) != len(snapshots):
                raise ValueError('Duplicate public registry identities')
            for component in d['components'].values():
                if not set(component['source_ids']) <= source_ids or not set(component['snapshot_ids']) <= snapshot_ids:
                    raise ValueError('Unresolved component provenance reference')
            for snapshot in snapshots:
                acquisition = read_json(root / relative_path(snapshot['acquisition_config_path']))
                if (snapshot['sha256'] != acquisition['expected_sha256']
                        or snapshot['acquisition_config_id'] != acquisition['acquisition_config_id']
                        or not set(snapshot['source_ids']) <= source_ids):
                    raise ValueError('Unresolved snapshot/acquisition identity')
    return {'status': 'PASS', 'release_fingerprint': identity['release_fingerprint'],
            'files': len(paths), 'scenarios': manifest['scenarios'], 'portability': 'PASS', 'checksums': 'PASS'}
