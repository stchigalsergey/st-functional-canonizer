"""Run typed-signature induction from an unpacked source checkout."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from st_signature_induction import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
