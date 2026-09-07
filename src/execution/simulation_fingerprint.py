from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass


def simulation_fingerprint(
    simulation: object,
) -> str:
    """
    Produce a deterministic SHA-256 fingerprint
    for a dataclass-based execution simulation.

    Serialization is intentionally strict. Unknown
    object types and non-finite JSON numbers fail
    rather than being coerced to strings.
    """

    if (
        not is_dataclass(simulation)
        or isinstance(simulation, type)
    ):
        raise TypeError(
            "Execution simulation must be "
            "a dataclass instance."
        )

    payload = json.dumps(
        asdict(simulation),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(
        payload
    ).hexdigest()
