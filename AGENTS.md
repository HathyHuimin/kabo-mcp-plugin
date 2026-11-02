# Kabo plugin repository

- Write commit messages, PR titles and PR descriptions in English. Code comments and the maintainer
  guides in `docs/` stay in Chinese. Plugin metadata and user-facing content are in English.
- The repository root is the plugin root. Maintain one `skills/` directory, one `kabo` entry and one
  build entry point. Codex, Claude Code and Cursor add only host metadata; do not copy business content
  or remove features for a particular host.
- The user selected fifteen Skills; see `skills.lock.json`. They were adapted from
  creator-skills and are now edited directly in this repository; do not overwrite them with a new
  creator-skills import. The lock file records only the starting point of the adaptation.
  Follow `docs/skills-spec.md` when changing a Skill. Enforcement stays off: keep `enforced` in
  `skills-spec.json` empty and do not add entries.
  Default builds and verification read only this repository. They do not read neighboring
  repositories or download Skills at install or run time.
- The Kabo service, not this repository, owns the API, OAuth, database, web app and deployment.
  Research judgments, scripts and reports run in the AI host. The plugin runs independently of Kabo's
  web agent and never calls it, directly or through the API, a queue or another proxy.
  They share no sessions, tasks, model loop, sandbox or report chain; only the Skills share an origin.
- The only production MCP connection is `/mcp-pub`. Each data operation is pinned and must match the
  provider's artifacts. Do not introduce a registry, a general-purpose executor or remote end-to-end
  research tasks. The provider keeps the old dynamic server protocol compatible.
- No lifecycle hooks. Save complete MCP responses explicitly and verify digests, run ownership and
  reports; do not read host session records. The static package version and the source Skills'
  business versions are maintained separately, and packaged content never updates itself at run time.
- The package must not contain apps, `.app.json`, a private registry, real local paths, test URLs or
  credentials. The host manages user authentication. A public signature-verification key may be
  packaged, but a content digest must not be presented as a digital signature or server attestation.
- Use Node 24 to run `npm ci --ignore-scripts` and `npm run verify`; use Python 3 to check the static
  scripts. A release ZIP reads only a full commit and must not include working-tree changes. Every
  Skill resource must be packaged in full.
- Code goes through PR review; never push directly to main. Do not deploy automatically, upload to a
  marketplace console or Publish. CI, a buildable ZIP, real-host and production acceptance, and
  marketplace review are separate facts. State any missing acceptance explicitly and never claim the
  plugin is ready for listing.
