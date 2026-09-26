# Delve Trading Agent repository operating contract

This repository contains a real-capital autonomous trading system. These permanent engineering rules apply to all future coding-agent work.

## 1. Engineering priorities

In this exact order:
1. correctness
2. capital preservation
3. fail-closed behavior
4. recovery safety
5. auditability
6. simple authority boundaries
7. deterministic behavior where practical
8. maintainability
9. performance
Never trade correctness or safety for convenience, test speed, architectural neatness, or feature velocity.

## 2. Real-capital safety rules

- Unknown state must never silently become PASS, zero exposure, free cash, no position, no obligation, or successful execution.
- Do not weaken a safety, freshness, risk, recovery, authorization, reservation, reconciliation, or execution gate merely to make tests pass.
- Do not create hidden retries around consequential writes.
- Preserve explicit durable authority transitions.
- Preserve recovery-first semantics.
- Preserve the distinction between observation, authorization, signing, submission, reconciliation, accounting, and recovery.
- Never combine these into a generic execute_order() abstraction that hides authority transitions.
- Existing ambiguity must remain fail-closed until venue-specific evidence proves the correct state.

## 3. Production restrictions

Unless the user explicitly authorizes a separate production task:
- do not deploy;
- do not access or modify Railway;
- do not change deployment variables;
- do not enable or disable operational kill switches;
- do not submit real orders;
- do not fund or move assets;
- do not modify live databases;
- do not start production trading processes;
- do not alter live account state.
Repository development does not imply production authorization.

## 4. Secret handling

- Never print, expose, copy, transform, inspect, commit, summarize, or request private keys, seed phrases, API secrets, .env values, Railway tokens, signer material, or authentication credentials.
- Do not add secret-bearing files to Git.
- Preserve and strengthen secret-ignore rules when relevant.
- Configuration examples may contain variable names only, never real values.

## 5. Git and baseline protection

- pump-live-baseline-2c87449 is the immutable reference for the pre-migration Pump live system.
- Do not rewrite, move, delete, or retarget that tag.
- Do not force-push.
- Do not rewrite published history.
- Do not modify main during Delve Trading Agent migration unless explicitly instructed.
- Perform migration work on refactor/delve-trading-agent-v1 or a later explicitly authorized branch.
- Before consequential changes, verify branch, working-tree state, and relevant baseline assumptions.
- Do not stage, commit, push, merge, rebase, switch branches, create or delete branches/tags, or otherwise mutate Git state unless the current task explicitly authorizes that operation.
- Never run destructive Git or filesystem operations such as `git reset --hard`, `git clean -fd`, destructive checkout/restore commands, or recursive deletion as a shortcut. Any such operation requires explicit user authorization and prior verification of what would be destroyed.

## 6. Existing Pump system compatibility

Pump/Solana is an existing venue implementation that must remain recoverable while the venue-neutral architecture is introduced.
During infrastructure migration:
- do not reinterpret existing persisted records;
- do not silently change hashes, fingerprints, status meanings, identity semantics, version contracts, or recovery inputs;
- do not alter strategy thresholds;
- do not alter exit economics;
- do not rename persisted schema merely for terminology consistency;
- do not delete legacy recovery paths;
- do not rename /tmp/delve-meme-agent-live-authority.lock without a separately reviewed authority migration;
- do not assume a branding rename permits a second process authority domain.
Durable backward compatibility takes priority over aesthetic cleanup.

## 7. Delve Trading Agent architecture direction

The target architecture is a venue-neutral trading core with isolated venue implementations.
Expected conceptual boundaries include:
- market source / normalized observation;
- strategy;
- candidate/intention;
- venue-specific evidence;
- portfolio/account risk;
- capital reservation;
- execution quotation;
- order authorization;
- venue execution;
- fill/receipt verification;
- accounting;
- position management;
- recovery;
- audit.
Do not distribute if venue == ... conditionals throughout the core.
Prefer explicit contracts and dependency injection at venue boundaries.
Pump-specific concepts such as:
- bonding curves;
- mints as universal identities;
- lamports as universal money;
- Solana pubkeys as universal account IDs;
- Helius;
- blockhashes;
- slots;
- PDAs/ATAs;
- Pump protocol/creator fees;
- mayhem_mode;
- Pump instruction construction;
must not become assumptions of the generic core.

## 8. Venue semantics

Never assume different venues share execution semantics.
In particular, do not assume Coinbase, Robinhood, Solana, or Pump share meanings for:
- accepted order;
- open order;
- partial fill;
- complete fill;
- cancel;
- rejection;
- expiry;
- retry safety;
- execution identity;
- account balance;
- available cash;
- reserved cash;
- settlement;
- fee timing;
- market-data sequence;
- recovery proof.
Every venue adapter must define and test its own lifecycle semantics.

## 9. Units and identities

Avoid ambiguous primitives in new generic code.
- Do not represent generic capital as “lamports.”
- Do not assume symbols are globally unique.
- Do not assume a mint or ticker uniquely identifies an instrument across venues or environments.
- New generic identity contracts should be venue- and environment-scoped.
- Monetary and quantity precision must be explicit and asset/instrument-aware.
- Avoid silent float conversion for authoritative financial accounting.

## 10. Strategy separation

The current Pump model and its research remain Pump-specific.
In particular:
- mayhem_mode;
- buy_rate_15s;
- probability_2x_15m;
- current Pump thresholds;
are not generic Delve Trading Agent concepts.
Infrastructure refactoring must not alter strategy behavior.
A Coinbase strategy or Robinhood strategy must be separately researched, validated, frozen, and forward-tested.

## 11. Coding discipline

- Prefer small, reviewable changes.
- One architectural boundary at a time.
- Do not perform opportunistic refactors unrelated to the task.
- Avoid broad renames during authority or persistence migrations.
- New core modules should have inert imports: importing them must not read .env, open SQLite, initialize RPC, contact networks, acquire locks, or start background work.
- Explicit types/contracts are preferred over loosely structured dictionaries at authority boundaries.
- Keep venue-specific imports out of generic modules.
- Do not silently swallow exceptions.
- Error states must remain observable and auditable.

## 12. Testing requirements

The canonical complete unit-test command is:

```sh
python -m unittest discover -s tests -p 'test_*.py'
```

python -m unittest by itself is not the repository’s full test suite.
For code changes:
- add focused regression tests for the behavior being introduced or changed;
- run the narrowest relevant focused tests first;
- run the full suite before accepting a consequential migration change;
- run Python compilation validation where appropriate;
- run git diff --check;
- report exact test counts and failures;
- never fix a failing safety test by weakening the invariant it protects.

## 13. Database and persistence discipline

- Schema changes involving live authority require separate review.
- Existing live recovery obligations must remain interpretable.
- Migrations must be explicit, deterministic, and backward-aware.
- Do not treat missing tables/rows as benign unless the contract explicitly defines that behavior.
- Do not manually edit live capital records as a shortcut around lifecycle logic.

## 14. Agent task behavior

For each task:
- respect the requested scope;
- inspect before modifying;
- identify safety-sensitive impacts;
- do not broaden the task without authorization;
- summarize files changed;
- summarize behavioral changes;
- report tests/verification performed;
- identify unresolved risks or assumptions.
If instructions conflict with capital safety, durable recovery, or explicit repository invariants, stop and surface the conflict rather than guessing.
