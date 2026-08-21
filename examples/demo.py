from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from st_canonizer_v2 import canonize  # noqa: E402


SOURCE = """
PROGRAM Demo
VAR
    set_signal : BOOL;
    reset_signal : BOOL;
    fault_memory : BOOL;
END_VAR

IF set_signal THEN
    fault_memory := TRUE;
END_IF
IF reset_signal THEN
    fault_memory := FALSE;
END_IF
END_PROGRAM
"""


if __name__ == "__main__":
    print(canonize(SOURCE))
