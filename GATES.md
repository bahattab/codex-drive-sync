# Acceptance gates

- [x] G1: Read-only environment and bounded native feasibility recorded. COMPATIBILITY.md; original Codex data untouched.
- [x] G2: Deterministic encrypted sync preserves changes/conflicts. Windows PowerShell/Python3.14.2, 69 tests PASS, exit0; TEST_RESULTS.md.
  CHECK: python -m unittest discover -s tests -q
  EXPECT: OK
- [x] G3: OAuth/secure local credentials/Drive implemented and reviewed. Provider tests + actual Windows keyring roundtrip.
- [x] G4: Installed CLI/service/MCP and Windows install/uninstall verified. Actual scripts exit0; desktop UI invocation remains NOT RUN separately.
- [x] G5: Independent critical/major findings repaired. REVIEW.md and regressions.
- [x] G6: Explicit consent and real encrypted Drive transfer PASS. verify_drive.py exit0; synthetic payloads in two temporary local environments. Live races NOT RUN.
- [ ] G7: Native UI continuation and actual two-device coherent-state acceptance. Native import BLOCKED; laptop NOT RUN pending operator results. Full continuity not achieved.
- [x] G8: Allowlisted source/history publication audited; GitHub main matched local source commit 3bcb49bd74bd990b201f61873182a8cf22e4ac58. OAuth/user state/keys excluded; 42 source files supplementary scan PASS. Final packaging metadata updates published separately.
