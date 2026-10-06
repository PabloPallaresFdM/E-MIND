"""Read-only checks for immutable snapshot objects."""
import hashlib
from pathlib import Path


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def verify_raw(objects):
    for obj in objects:
        path = Path(obj['raw_path'])
        if (path.is_symlink() or not path.is_file()
                or path.stat().st_size != obj['size_bytes']
                or digest(path) != obj['sha256']):
            raise ValueError('Raw integrity mismatch: ' + str(path))
