# Security

Report vulnerabilities privately through the repository's GitHub **Security → Report a vulnerability** feature once enabled. If private reporting is unavailable, contact the maintainer through a verified private channel; do not post credentials or an exploit containing private records in an issue. No response-time SLA or bug-bounty payment is promised.

Include affected version/commit, reproduction, impact, and a minimal redacted example. Agree on disclosure timing before publishing exploit details.

## Trust boundaries

- Provider keys are server-side secrets. The keyless demo never requests or uses them.
- Model output and fetched documents are untrusted inputs. Semantic review is not a security sandbox or a proof of truth.
- Local environment loaders can discover `.env` files. Run untrusted forks in an isolated directory without production credentials.
- Forecast ledgers are tamper-evident only against a trusted retained head. They do not supply independent timestamps or protect against a writer rebuilding history. Use a single writer; cross-process locking is not guaranteed.
- Data collectors access third-party services; inspect source terms, resource use and endpoints before running. Keep local data out of public release bundles.
- `scripts/release.py` scans an explicit allowlist. Its credential patterns are a guard, not a complete secret-detection guarantee. Review the manifest and use independent secret scanning before publication.

Until versioned maintenance branches are announced, fixes target the current main branch only. Do not deploy old versions with production credentials merely because tests pass.
