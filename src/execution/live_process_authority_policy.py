from __future__ import annotations

from pathlib import Path


LIVE_PROCESS_AUTHORITY_POLICY_VERSION = (
    "live-process-authority-policy-v1"
)

LIVE_PROCESS_AUTHORITY_LOCK_PATH = Path(
    "/tmp/delve-meme-agent-live-authority.lock"
)


def live_process_authority_lock_path(
) -> Path:
    """
    Return the canonical same-host live-authority lock path.

    Version 1 deliberately defines one host-global Delve live
    authority domain.

    The path is intentionally:
      - absolute;
      - independent of the working directory;
      - independent of database path;
      - independent of wallet configuration;
      - not environment-configurable.

    This prevents separate processes from bypassing exclusivity
    through differing DB paths, wallet settings, checkouts, or
    process environments.

    This policy defines only the authority domain. It does not:
      - acquire or release the process lease;
      - create directories or files;
      - access the database;
      - load a signer;
      - start recovery;
      - invoke BUY or SELL execution.
    """
    return LIVE_PROCESS_AUTHORITY_LOCK_PATH
