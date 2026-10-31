import argparse
from importlib.metadata import version


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vigilant",
        description="Detect documentation drift caused by code changes.",
    )
    parser.add_argument(
        "--version", action="version", version=f"vigilant {version('vigilant')}"
    )
    parser.parse_args()
    parser.print_help()
