from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
PROCFILE = ROOT / "Procfile"
GITIGNORE = ROOT / ".gitignore"


EXPECTED_ENVIRONMENT_NAMES = {
    "DELVE_LIVE_DB_PATH",
    "DELVE_LIVE_RECOVERY_INTERVAL_SECONDS",
    "DELVE_LIVE_OPERATIONAL_KILL",
    "DELVE_LIVE_MAX_TRADE_EQUITY_BPS",
    "DELVE_LIVE_MAX_TOTAL_EXPOSURE_BPS",
    "DELVE_LIVE_MAX_DAILY_LOSS_BPS",
    "DELVE_LIVE_MAX_DRAWDOWN_BPS",
    "DELVE_LIVE_MAX_OPEN_POSITIONS",
    "DELVE_LIVE_MIN_TRADE_LAMPORTS",
    "DELVE_LIVE_MAX_SIZE_PRICE_IMPACT_BPS",
    "DELVE_LIVE_BUY_EXECUTION_WALLET_PUBKEY",
    "DELVE_LIVE_BUY_EXECUTION_PROTECTED_CASH_LAMPORTS",
    "DELVE_LIVE_BUY_EXECUTION_BUY_SLIPPAGE_BPS",
    "DELVE_LIVE_BUY_EXECUTION_BUY_BASE_NETWORK_FEE_LAMPORTS",
    "DELVE_LIVE_BUY_EXECUTION_BUY_PRIORITY_FEE_LAMPORTS",
    "DELVE_LIVE_BUY_EXECUTION_BUY_RENT_LAMPORTS",
    "DELVE_LIVE_BUY_EXECUTION_EXIT_SLIPPAGE_BPS",
    "DELVE_LIVE_BUY_EXECUTION_EXIT_BASE_NETWORK_FEE_LAMPORTS",
    "DELVE_LIVE_BUY_EXECUTION_EXIT_PRIORITY_FEE_LAMPORTS",
    "DELVE_LIVE_BUY_EXECUTION_RESERVATION_TTL_SECONDS",
    "DELVE_LIVE_BUY_EXECUTION_MAX_AUTHORIZATION_AGE_SECONDS",
    "DELVE_LIVE_BUY_EXECUTION_COMPUTE_UNIT_LIMIT",
    "DELVE_LIVE_ENTRY_EVIDENCE_MIN_PROBABILITY_2X_15M",
    "DELVE_LIVE_ENTRY_EVIDENCE_MAX_CANDIDATE_AGE_SECONDS",
    "DELVE_LIVE_ENTRY_EVIDENCE_MAX_CONCURRENCY",
    "DELVE_LIVE_ENTRY_EVIDENCE_MAX_PENDING_TASKS",
    "DELVE_LIVE_SELL_SUPERVISOR_TAKE_PROFIT_RETURN_BPS",
    "DELVE_LIVE_SELL_SUPERVISOR_STOP_LOSS_RETURN_BPS",
    "DELVE_LIVE_SELL_SUPERVISOR_MAX_HOLD_SECONDS",
    "DELVE_LIVE_SELL_SUPERVISOR_EVALUATION_INTERVAL_SECONDS",
    "HELIUS_API_KEY",
    "DELVE_SOLANA_KEYPAIR_BASE58",
}


def parse_environment_template() -> dict[str, str]:
    values: dict[str, str] = {}

    for raw_line in ENV_EXAMPLE.read_text().splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            raise AssertionError("environment line missing equals")

        name, value = line.split("=", 1)

        if name in values:
            raise AssertionError(f"duplicate environment name: {name}")

        values[name] = value

    return values


class LiveDeploymentContractTests(unittest.TestCase):
    def test_environment_template_has_exact_required_surface(self):
        values = parse_environment_template()

        self.assertEqual(set(values), EXPECTED_ENVIRONMENT_NAMES)
        self.assertEqual(len(values), 32)

    def test_environment_template_contains_no_values_or_secrets(self):
        values = parse_environment_template()

        self.assertTrue(all(value == "" for value in values.values()))

    def test_procfile_uses_only_unified_live_launcher(self):
        lines = [
            line.strip()
            for line in PROCFILE.read_text().splitlines()
            if line.strip()
        ]

        self.assertEqual(
            lines,
            ["worker: python -m src.execution.live_entry_process_launcher"],
        )

    def test_private_runtime_state_remains_gitignored(self):
        lines = {
            line.strip()
            for line in GITIGNORE.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        }

        self.assertIn(".env", lines)
        self.assertIn("logs/", lines)


if __name__ == "__main__":
    unittest.main()
