import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "services" / "common"))
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "services" / "drift"))
sys.path.insert(0, str(ROOT / "services" / "ml"))

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(
    0,
    str(ROOT / "services" / "api")
)

from spilltrace_api.case import CaseStore
from spilltrace_api.reports import generate_incident_report


store = CaseStore()

case = store.load("demo")

if case is None:
    raise RuntimeError("Demo case not found")

path = generate_incident_report(
    case,
    case_number="INC-DEMO-0001",
)

print("Report generated:")
print(path)