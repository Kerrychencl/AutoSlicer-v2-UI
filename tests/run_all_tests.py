"""Master test runner for AutoSlicer Desktop Workbench verification."""
import os
import sys
import unittest
from pathlib import Path

# Force headless execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUNBUFFERED"] = "1"

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

import tests.test_config as t_config
import tests.test_scanner as t_scanner
import tests.test_runner as t_runner
import tests.test_queue_manager as t_queue
import tests.test_dashboard_page as t_dash
import tests.test_ui_smoke as t_smoke
import tests.test_cli_scripts as t_cli


def build_suite() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromModule(t_config))
    suite.addTests(loader.loadTestsFromModule(t_scanner))
    suite.addTests(loader.loadTestsFromModule(t_runner))
    suite.addTests(loader.loadTestsFromModule(t_queue))
    suite.addTests(loader.loadTestsFromModule(t_dash))
    suite.addTests(loader.loadTestsFromModule(t_smoke))
    suite.addTests(loader.loadTestsFromModule(t_cli))

    return suite


def main() -> int:
    print("=" * 70)
    print(" AutoSlicer Desktop Workbench - Verification & Quality Assurance Suite")
    print("=" * 70)

    suite = build_suite()
    print(f"Loaded {suite.countTestCases()} unit and integration tests across 6 modules.\n")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    print(f"Ran: {result.testsRun} tests")
    print(f"Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("=" * 70)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
