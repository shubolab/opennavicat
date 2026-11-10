"""PyInstaller entry point; keep package imports working in frozen builds."""
from opennavicat.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
