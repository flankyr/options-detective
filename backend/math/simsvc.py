"""Entry shim: `od-math simsvc` -> sim.runner.main (keeps the flat entry-point layout)."""
from sim.runner import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
