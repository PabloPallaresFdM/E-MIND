"""Byte-preserving public HTTP retrieval. Python standard library only.

Input is a reviewed JSON/YAML-1.2 request object. No scientific selection,
timestamp conversion, unit conversion or harmonisation is implemented here.
Existing snapshots are never overwritten; use a new snapshot_id for a revision.
"""
import argparse
import csv
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import urllib.request
from urllib.parse import urlsplit, parse_qsl
import xml.etree.ElementTree as ET
import zipfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts/source_tools'))
from registry_schema import check_no_secrets, SENSITIVE_KEY

ROOT = Path(os.environ['EMIND_DATA_ROOT']) if os.environ.get('EMIND_DATA_ROOT') else None
EXECUTION_HOST = os.environ.get('EMIND_EXECUTION_HOST')
APPROVED = {'de_smard_electrical_load': 'load', 'de_smard_day_ahead': 'market',
            'eu_ec_weekly_oil_bulletin_diesel': 'fuel'}
ALLOWED_HOSTS = {'www.smard.de', 'energy.ec.europa.eu'}


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def public_url(url):
    check_no_secrets(url)
    parsed = urlsplit(url)
    if parsed.username is not None or parsed.password is not None or any(
            SENSITIVE_KEY.search(k) for k, _ in
            parse_qsl(parsed.query, keep_blank_values=True) +
            parse_qsl(parsed.fragment, keep_blank_values=True)):
        raise ValueError('Credential-bearing URL is forbidden')
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError('Only reviewed official HTTPS hosts are allowed')


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def inspect_format(path, kind):
    """Read-only structural audit; retain source labels/values unchanged."""
    path = Path(path)
    with path.open('rb') as stream:
        prefix = stream.read(1024).lstrip().lower()
    if not path.stat().st_size or b'<html' in prefix or b'<!doctype html' in prefix:
        raise ValueError('Empty payload or HTML response is not scientific data')
    if kind == 'xlsx':
        with zipfile.ZipFile(path) as book:
            corrupt = book.testzip()
            if corrupt:
                raise ValueError('Workbook ZIP CRC failure')
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            tree = ET.fromstring(book.read('xl/workbook.xml'))
            sheets = [dict(x.attrib) for x in tree.findall('s:sheets/s:sheet', ns)]
            return {'format': kind, 'zip_crc_valid': True, 'sheets': sheets,
                    'audit_scope': 'ZIP integrity and workbook sheet metadata only; no row extraction'}
    if kind == 'csv':
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = csv.reader(stream, delimiter=';')
            header = next(rows)
            if len(header) < 2:
                raise ValueError('Expected a semicolon-delimited SMARD export')
            count = 0
            first = last = None
            widths = set()
            for row in rows:
                count += 1
                widths.add(len(row))
                first = row if first is None else first
                last = row
            if not count or widths != {len(header)}:
                raise ValueError('Empty or inconsistent CSV schema')
            return {'format': kind, 'header': header, 'row_count': count,
                    'first_row_verbatim_fields': first, 'last_row_verbatim_fields': last}
    raise ValueError('Unreviewed payload format')


def download(config):
    check_no_secrets(config)
    if ROOT is None or not ROOT.is_absolute() or not EXECUTION_HOST:
        raise ValueError('Explicit absolute data root and execution host required')
    if socket.getfqdn() != EXECUTION_HOST:
        raise ValueError('Retrieval must run on the configured execution host')
    source = config['source_id']
    if source not in APPROVED or config['scenario_id'] != 'DE_CENT':
        raise ValueError('Outside approved source/scenario scope')
    if source.startswith('de_smard_') and config.get('semantic_freeze_status') != 'VERIFIED':
        raise ValueError('SMARD semantic freeze must precede retrieval')
    public_url(config['url'])
    expected_host = 'www.smard.de' if source.startswith('de_smard_') else 'energy.ec.europa.eu'
    if urlsplit(config['url']).hostname != expected_host:
        raise ValueError('Source/provider host mismatch')
    snapshot = config['snapshot_id']
    if not snapshot or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in snapshot):
        raise ValueError('Invalid snapshot identifier')
    directory = ROOT / 'raw/DE_CENT' / APPROVED[source] / snapshot
    os.umask(0o077)
    directory.mkdir(mode=0o700)  # exclusive: never overwrite an old snapshot
    payload = config.get('request_body_utf8')
    body = payload.encode('utf-8') if payload is not None else None
    headers = {'User-Agent': 'E-MIND/0.1 controlled raw pilot', 'Accept-Encoding': 'identity'}
    headers.update(config.get('request_headers', {}))
    check_no_secrets(headers)
    request = urllib.request.Request(config['url'], data=body, headers=headers,
                                     method=config.get('method', 'GET'))
    started = utc()
    temporary = ROOT / 'tmp' / (snapshot + '.partial')
    try:
        with urllib.request.build_opener(PublicRedirect()).open(request, timeout=60) as response:
            public_url(response.geturl())
            if response.status != 200 or response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise ValueError('Unexpected status or encoded payload; no automatic decoding')
            name = response.headers.get_filename() or config.get('original_filename')
            if not name or Path(name).name != name or name in ('.', '..'):
                raise ValueError('Missing or unsafe original filename')
            destination = directory / name
            http = {k: response.headers[k] for k in ('Content-Type', 'Content-Length',
                    'Content-Disposition', 'ETag', 'Last-Modified', 'Date', 'Content-Encoding')
                    if response.headers.get(k) is not None}
            digest = hashlib.sha256()
            size = 0
            with temporary.open('xb') as stream:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > config.get('maximum_bytes', 100 * 1024 * 1024):
                        raise ValueError('Reviewed object byte limit exceeded')
                    digest.update(block)
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
            if 'Content-Length' in http and int(http['Content-Length']) != size:
                raise ValueError('Incomplete HTTP payload')
            audit = inspect_format(temporary, config['format'])
            if sha256(temporary) != digest.hexdigest():
                raise ValueError('Post-retrieval checksum mismatch')
            record = dict(config)
            record.update({'retrieval_started_at_utc': started, 'retrieved_at_utc': utc(),
                           'host': socket.getfqdn(), 'request_headers': headers,
                           'final_url': response.geturl(), 'http_status': response.status,
                           'http_metadata': http, 'original_filename': name,
                           'raw_path': str(destination), 'size_bytes': size,
                           'sha256': digest.hexdigest(), 'content_type': http.get('Content-Type'),
                           'immutable': True, 'transformation_performed': False,
                           'validation_status': 'PASS_FORMAT_AND_SHA256', 'format_audit': audit})
        check_no_secrets(record)
        os.link(str(temporary), str(destination))  # fails rather than overwrite
        temporary.unlink()
        destination.chmod(0o400)
        sidecar = Path(str(destination) + '.manifest.json')
        with sidecar.open('x') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        sidecar.chmod(0o400)
        print(json.dumps({'raw_path': str(destination), 'sha256': record['sha256'],
                          'size_bytes': size, 'format_audit': audit}, ensure_ascii=False))
        return record
    except Exception as error:
        # Retain any partial bytes in tmp, never certify or silently replace them.
        failure = {'snapshot_id': snapshot, 'at_utc': utc(), 'exception_type': type(error).__name__,
                   'status': 'FAILED_NOT_ACCEPTED', 'request_config': config,
                   'partial_path': str(temporary) if temporary.exists() else None}
        with (directory / 'failure.manifest.json').open('x') as stream:
            json.dump(failure, stream, indent=2)
            stream.write('\n')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    args = parser.parse_args()
    download(json.loads(args.config.read_text(encoding='utf-8')))
