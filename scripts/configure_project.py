"""
Developer fallback for the v2.8 UI configurator.

Writes missing example config files and validates the merged local config.
Use the browser configurator for normal setup.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.core import project_config

CONFIGURATOR_URL = "http://localhost:8000/configurator"


def main() -> int:
    written = project_config.write_example_configs()
    validation = project_config.validate_config()

    print("ResolveKit configurator")
    print("=" * 34)
    print()
    print("Browser UI:")
    print("  1. Run:  python start.py")
    print(f"  2. Open: {CONFIGURATOR_URL}")
    print()
    print("This script is only the terminal fallback. It creates missing example")
    print("config files and validates the merged YAML config.")
    print()

    if written:
        print("Created example config files:")
        for path in written:
            print(f"  - {path}")
    else:
        print("Example config files already exist.")

    print("\nValidation:")
    if validation["valid"]:
        print("  OK")
    else:
        for error in validation["errors"]:
            print(f"  ERROR: {error}")
    for warning in validation["warnings"]:
        print(f"  WARNING: {warning}")

    return 0 if validation["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
