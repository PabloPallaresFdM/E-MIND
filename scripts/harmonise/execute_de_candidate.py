"""Lossless scientific CSV/JSON serialization; no execution or acquisition CLI."""
import csv
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src'))
from emind.providers.integrity import digest

def serial(value):
    return value.isoformat().replace('+00:00', 'Z') if isinstance(value, datetime) else str(value)

def save_csv(path, rows):
    fields = list(rows[0])
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: serial(v) if isinstance(v, (datetime, Decimal)) else v for k,v in row.items()})
    path.chmod(0o400)
    # Independent read-back verifies lossless field serialization and row count.
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != fields:
            raise ValueError('Output schema changed during write')
        count = 0
        for expected, actual in zip(rows, reader):
            for key, value in expected.items():
                if actual[key] != ('' if value is None else serial(value)):
                    raise ValueError('Output round-trip mismatch')
            count += 1
        if count != len(rows) or next(reader, None) is not None:
            raise ValueError('Output row count changed')
    return {'path': str(path), 'sha256': digest(path), 'size_bytes': path.stat().st_size,
            'rows': len(rows), 'fields': fields}

def save_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, default=serial); stream.write('\n')
    path.chmod(0o400)
    return {'path': str(path), 'sha256': digest(path), 'size_bytes': path.stat().st_size}
