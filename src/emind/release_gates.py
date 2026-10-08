"""Offline local RC preflight; never grants authority to publish."""
import hashlib
import json
import re
from pathlib import Path

TITLE = 'E-MIND — European Microgrid Intelligence for Next-generation Decision-making'
# Historical package citation identity; retained only for verification compatibility.
LEGACY_TITLE = 'E-MIND — European Intelligent Microgrid Energy Decision Benchmark'
REPOSITORY = 'https://github.com/PabloPallaresFdM/E-MIND'
RC_VERSION = r'\d+\.\d+\.\d+-rc\.[1-9]\d*'
STATE = {'release_stage': 'release_candidate', 'publication_status': 'NOT_ISSUED',
         'doi': None, 'archival_status': 'not_archived'}
CLOSED_ITEMS = {'SMARD licensing', 'ERA5 licensing', 'EC fuel scoped redistribution',
                'code license', 'metadata license', 'authorship', 'CITATION', 'exporter gates'}


def validate_citation(content, version, authors):
    import yaml
    from jsonschema import Draft7Validator, FormatChecker
    # Reject duplicate keys rather than allowing YAML's last-key-wins semantics.
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node):
        pairs = loader.construct_pairs(node, deep=True)
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate citation key')
            result[key] = value
        return result
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        citation = yaml.load(content, Loader=UniqueLoader)
        schema_bytes = Path(__file__).with_name('cff_schema_1.2.0.json').read_bytes()
        if hashlib.sha256(schema_bytes).hexdigest() != '0b8d22140da702d766df318dcff3a91af2f39521298dcf36d76315fd99cc169b':
            raise ValueError('Frozen official CFF schema changed')
        schema = json.loads(schema_bytes)
        Draft7Validator(schema, format_checker=FormatChecker()).validate(citation)
    except Exception as exc:
        raise ValueError('Invalid/missing CITATION.cff') from exc
    if (citation.get('title') not in (TITLE, LEGACY_TITLE) or citation.get('version') != version
            or citation.get('repository-code') != REPOSITORY or citation.get('authors') != authors
            or any(k in citation for k in ('doi', 'date-released'))):
        raise ValueError('Citation disagrees with frozen local RC metadata')
    return citation


def validate_rc_gates(spec):
    """Validate all owner/provider/state gates before any destination is created."""
    version = spec['release_version']
    if not re.fullmatch(RC_VERSION, version):
        raise ValueError('Expected non-dev release candidate version')
    metadata = spec['metadata']
    dataset = metadata.get('dataset.json', {})
    if dataset.get('release_version') != version:
        raise ValueError('Dataset RC version mismatch')
    if any(k not in dataset or dataset[k] != v for k, v in STATE.items()):
        raise ValueError('Explicit NOT_ISSUED/null DOI/not_archived RC state required')
    authors = dataset.get('authors')
    if (not isinstance(authors, list) or not authors or dataset.get('authorship_status') != 'FROZEN'
            or any(not isinstance(a, dict) or not a.get('given-names') or not a.get('family-names') for a in authors)):
        raise ValueError('Non-empty frozen authorship required')
    licenses = metadata.get('licenses.json', {})
    for field, grant, object_name in [('code_license', 'MIT', 'project_code'),
                                      ('metadata_license', 'CC-BY-4.0', 'original_metadata')]:
        entry = licenses.get(object_name, {})
        if (licenses.get(field) != grant or entry.get('current_license') != grant
                or entry.get('status') != 'CLOSED' or not entry.get('rights_holder')):
            raise ValueError(f'Resolved {field} required')
    if licenses.get('upstream_relicensed') is not False:
        raise ValueError('Upstream licenses must remain separate')
    if licenses.get('publication_status') != 'NOT_ISSUED':
        raise ValueError('License publication state mismatch')
    source_registry = metadata.get('source_registry.json', {}).get('sources', [])
    sources = licenses.get('sources', [])
    source_ids = {s['source_id'] for s in source_registry}
    required = {sid for s in spec['scenarios'] for c in s['scenario']['components'].values()
                for sid in c.get('source_ids', [])}
    if (not required or required != source_ids or len(source_ids) != len(source_registry)
            or {s.get('source_id') for s in sources} != required or len(sources) != len(required)):
        raise ValueError('Missing/duplicate per-source license status')
    for source in sources:
        if (source.get('status') != 'CLOSED' or not source.get('license_name')
                or not source.get('license_url') or not source.get('attribution')
                or source.get('derived_bundling_status') != 'CLEARED_SCOPED_WITH_CONDITIONS'
                or source.get('raw_class') != 'R2'):
            raise ValueError('Unresolved blocking source license/attribution')
        registry = next(s for s in source_registry if s['source_id'] == source['source_id'])
        if any(registry.get(k) != source.get(k) for k in ('attribution', 'license_name', 'license_url')):
            raise ValueError('Provider attribution/terms inconsistent')
    # No stale A3 blockers in acquisition, registry or any other public metadata.
    def stale(value):
        if isinstance(value, dict):
            return any(stale(v) for v in value.values())
        if isinstance(value, list):
            return any(stale(v) for v in value)
        return isinstance(value, str) and ('PENDING' in value or value == 'BLOCKING')
    if stale(metadata) or spec.get('publication_items') != []:
        raise ValueError('Stale/unresolved blocking publication entry')
    blockers = dataset.get('publication_blockers', [])
    closed = {x.get('item') for x in blockers if x.get('status') == 'CLOSED'}
    if closed != CLOSED_ITEMS or len(blockers) != len(CLOSED_ITEMS) + 2:
        raise ValueError('Publication blocker closure manifest incomplete')
    optional = {x.get('item') for x in blockers if x.get('status') == 'NON-BLOCKING'}
    if optional != {'DOI absent', 'Zenodo absent'}:
        raise ValueError('Explicit non-blocking archive state required')
    texts = spec.get('text_files', {})
    validate_citation(texts.get('CITATION.cff', ''), version, authors)
    holder = licenses['project_code']['rights_holder']
    if (f'Copyright (c) 2026 {holder}' not in texts.get('LICENSE', '')
            or 'Permission is hereby granted, free of charge' not in texts.get('LICENSE', '')
            or 'CC BY 4.0' not in texts.get('LICENSE-METADATA', '')
            or 'local release candidate / pre-release artifact' not in texts.get('README.md', '')):
        raise ValueError('License grant/local README missing or inconsistent')
    return dict(STATE, citation_status='VALID', publication_blockers=blockers)
