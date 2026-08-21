"""Print a stable fingerprint for a directory of ST source files."""

from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path


def fingerprint(root: Path):
    files = sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".st"),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    aggregate = sha256()
    for path in files:
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        aggregate.update(rel.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(sha256(data).hexdigest().encode("ascii"))
        aggregate.update(b"\n")
    return len(files), aggregate.hexdigest()


def main():
    parser = ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    count, digest = fingerprint(args.root.resolve())
    print(f"files={count}")
    print(f"sha256={digest}")


if __name__ == "__main__":
    main()
