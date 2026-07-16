"""Service entrypoint.

The startup sequence (config load, migrations, seed KB, model warmup, AMQP
consumers, HTTP server, signal handling) is defined in spec 01 §6 and is
implemented in a later phase. This module currently provides the console-script
entrypoint only.
"""

from __future__ import annotations


def main() -> None:
    """Run the analyzer-ng service (not yet implemented)."""
    raise NotImplementedError("analyzer-ng startup sequence is implemented in a later phase")


if __name__ == "__main__":
    main()
