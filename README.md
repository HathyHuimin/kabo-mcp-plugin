# Kabo plugin

Kabo helps you research public creator accounts, compare competitors, find trends, analyze videos,
develop content ideas and scripts, and remake a viral talking-head video with you in it. This
repository contains one plugin: a `kabo` MCP connection, fifteen bundled Skills, and the metadata
needed by each host. The AI host runs the workflows and writes
reports; Kabo provides the data tools. The plugin does not hand work to Kabo's web agent, download
Skills at runtime, or register lifecycle hooks.

The current GitHub package release is [**1.0.13**](https://github.com/kabo-sh/kabo-mcp-plugin/releases/tag/v1.0.13),
packaged as `kabo-1.0.13.zip`. Version 1.0.0 reset the public release numbering while retaining the latest
implementation. Earlier version history and source Skill versions remain separate. OpenAI marketplace
review and production acceptance remain separate from this GitHub release.

## Choose your installation

| Your setup | Install |
| --- | --- |
| You already connected Kabo MCP in your AI host | [Skills only](#skills-only): use Git, ZIP or the optional skills CLI to add the fifteen workflows and keep your existing connection. |
| You want the MCP connection and workflows together | [Full plugin](#installation-and-authentication): use your host's plugin installer. |

[MCP setup and Skills guide](https://kabo.sh/docs/mcp#skills) ·
[Latest release and ZIP download](https://github.com/kabo-sh/kabo-mcp-plugin/releases/latest)

<a id="skills-only-skills-cli"></a>

## Skills only

The MCP connection provides data tools. Skills add the research workflows your AI host runs with
those tools. Connecting MCP does not install Skills automatically. If the full Kabo plugin is
already installed, it includes all bundled Skills; installing them again can produce duplicate entries.

Official repository: **https://github.com/kabo-sh/kabo-mcp-plugin**.

Kabo Skills can be used by compatible agents on your computer, in a remote workspace or in the
cloud. The agent environment must load `SKILL.md` and its bundled files, run the required scripts
with Python 3, and use your authorized Kabo MCP connection. Compatibility depends on these
capabilities, not on whether the interface is a desktop app or a web page. Codex, Claude Code and
Cursor are examples, not an exclusive support list. See the host's Skills loading and execution
instructions and the [workflow requirements](#requirements-and-connection).

Choose Git, ZIP or the optional skills CLI according to your environment and preference.

### Option 1: Clone the GitHub repository

In the workspace where your agent can access Skill files:

```sh
git clone --branch stable --single-branch https://github.com/kabo-sh/kabo-mcp-plugin.git
```

Configure your agent to load the complete Skill folders under `kabo-mcp-plugin/skills/`, or copy or
import those folders into its supported Skills location. Keep all scripts, references and assets.
Cloning alone does not register Skills with the agent. Keep your existing Kabo MCP connection and
sign-in.

For a clone on `stable`, update from its parent directory with:

```sh
git -C kabo-mcp-plugin pull --ff-only
```

If you copied or imported the Skill folders elsewhere, replace those copies as well. Git and ZIP
installations are not tracked by the skills CLI updater.

### Option 2: Use the skills CLI (npx)

The [skills CLI](https://github.com/vercel-labs/skills) is an optional installer. You can also clone
the GitHub repository or download a ZIP. Choose whichever method fits your environment and preference.

After connecting `https://kabo.sh/mcp-pub` and signing in, run this in your project directory:

```sh
npx skills add 'kabo-sh/kabo-mcp-plugin#stable' --skill '*'
```

Select your agent in the installer. Examples of explicit CLI targets are listed below; this is not
a complete list of agents that can use Kabo Skills. Use Git or ZIP for compatible environments that
the CLI does not target.

| Agent | Option |
| --- | --- |
| Codex | `--agent codex` |
| Claude Code | `--agent claude-code` |
| Cursor | `--agent cursor` |

The command installs all bundled Skills with their scripts and resources. It does not add an MCP
connection or change your Kabo sign-in. **Keep your existing Kabo connection.** If you have not
connected yet, follow the [MCP setup guide](https://kabo.sh/docs/mcp#connect). For Codex, the command is
`codex mcp add kabo --url https://kabo.sh/mcp-pub`; to sign in again later, use
`codex mcp login kabo`.

Installation defaults to the current project. Add `-g` to install for all projects. For Codex, these
locations are `.agents/skills/` and `~/.agents/skills/`, respectively; other hosts use their own
supported locations. Run `npx skills list` (or `npx skills list -g`) to see the installation, then
start a new host session to load it.

### CLI versions and updates

The CLI records the source ref and a content hash in `skills-lock.json`, or in its global lock file
for `-g`. The repository's `skills.lock.json` is a separate record of the original Skill sources.

- `#stable` follows published releases. It advances only after a release; installed files stay as
  they are until you run an installation or update command.
- `#v1.0.13` pins one release. Updating does not switch to a newer tag; install the new tag explicitly.
- Without a ref, the CLI follows `main`, which includes unreleased changes.

See [GitHub Releases](https://github.com/kabo-sh/kabo-mcp-plugin/releases) for new versions and release
notes. To update installed Skills, choose the scope explicitly:

```sh
npx skills update -p
```

Use `npx skills update -g` for global installations. These commands update all tracked Skills in that
scope, including other sources. To refresh only Kabo, rerun the `skills add` command above with the
same agent and project/global scope.

**`skills check` is not a read-only check in CLI 1.7.4.** It runs the same update logic as `update` and
can replace installed files. Use the release page to read about updates without changing your
installation. See the [CLI 1.7.4 implementation](https://github.com/vercel-labs/skills/blob/v1.7.4/src/cli.ts#L420).

### Option 3: Download a ZIP

The [latest release](https://github.com/kabo-sh/kabo-mcp-plugin/releases/latest) includes a ZIP and its
SHA-256 file. This is the full plugin package. For manual Skills-only installation, extract it and
copy or import each complete directory under `skills/` into your agent's supported Skills location, including
all scripts, references and assets. Copying only `SKILL.md` is insufficient. Manual copies are not
tracked by the skills CLI updater.

The CLI collects anonymous usage data; set `DISABLE_TELEMETRY=1` to turn it off.

Installing the ten Skills of release 1.0.2 and their discovery by Codex 0.164 were checked on October 27, 2026. Kabo
sign-in and a complete Skill run through this method still need testing, as do other agents. See
[requirements and connection](#requirements-and-connection) for evidence downloads and local video
requirements.

## Installation and authentication

This repository also serves as a Claude Code and Codex marketplace. Each listing points to the same
`kabo` plugin at the repository root.

Hosts fetch the repository through local, noninteractive `git`.

| Host | Marketplace | Plugin ID |
| --- | --- | --- |
| Claude Code | `kabo-mcp-plugin` | `kabo@kabo-mcp-plugin` |
| Codex | `kabo-plugin-openai` | `kabo@kabo-plugin-openai` |

The marketplace names differ because existing Codex installations already reference
`kabo-plugin-openai`. Renaming it would break those references.

### Claude Code

In Claude Code 2.1.275 or later:

```text
/plugin install kabo --marketplace kabo-sh/kabo-mcp-plugin
```

Or add the marketplace first:

```text
/plugin marketplace add kabo-sh/kabo-mcp-plugin
/plugin install kabo@kabo-mcp-plugin
```

The terminal equivalents are `claude plugin marketplace add kabo-sh/kabo-mcp-plugin` and
`claude plugin install kabo@kabo-mcp-plugin`. If you do not have a GitHub SSH key, set
`CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` to clone over HTTPS.
For development, use `claude --plugin-dir <plugin-root>` to load the working directory directly.

### Codex

Codex 0.163 supports GitHub marketplace sources (`owner/repo[@ref]`, HTTPS or SSH):

```sh
codex plugin marketplace add kabo-sh/kabo-mcp-plugin
codex plugin add kabo@kabo-plugin-openai
```

If you already added `kabo-plugin-openai` from a local clone, run `git pull` there, then run
`codex plugin add kabo@kabo-plugin-openai` again. Codex caches plugins by version. Only one marketplace
can have this name; to switch to a GitHub source, remove the old listing with
`codex plugin marketplace remove kabo-plugin-openai`, then add the new source.

Kabo data tools use your Kabo data allowance. Codex asks for approval on each call by default. If you
want to approve these calls in advance, add this to `~/.codex/config.toml`:

```toml
[plugins."kabo@kabo-plugin-openai".mcp_servers.kabo]
default_tools_approval_mode = "approve"
```

This configuration has been tested. The equivalent command-line `-c` override did not work for the
plugin ID containing `@`.

### Cursor

Place the extracted package in a supported local plugin directory and reload it. The package uses the
portable format; installation in Cursor still needs to be tested.

### Validation status

Use `claude plugin validate <plugin-root>` to check the Claude Code manifest and marketplace. Codex
metadata is covered by portable-schema and package tests. Under the old version numbering, 3.1.0
passed an end-to-end YouTube video-analysis run in Codex 0.163. That historical result does not replace
acceptance testing for the current release; record results against the exact candidate commit and ZIP.

### Requirements and connection

The workflows need Python 3 and workspace file access. Local video analysis also needs ffmpeg and
ffprobe. Full evidence normally arrives through download links valid for 10 minutes on
`storage.googleapis.com`. The scripts download it and check its size and SHA-256. If downloads are
unavailable, the host must be able to export complete tool results for the `stage` fallback.
Sandbox network behavior still needs testing in each host. ChatGPT and Claude.ai web environments
require separate validation of these capabilities; a local installation does not establish web support.
A successful build also does not establish source-distribution rights or marketplace approval.

The only connection is `https://kabo.sh/mcp-pub`. Follow the host's OAuth flow on first use
(or during installation in Codex). The host manages login, refresh and revocation. Versions 1.0.0 and later keep
the `/mcp-pub` address introduced in the old 3.0.1 release. The former `/mcp-directory` endpoint was
removed and has returned 404 since October 22, 2026. Installations still using that address must update and reconnect. If `/mcp-pub` is unavailable,
report the failure rather than switching endpoints or reusing a token issued for another audience.

**Repeated `invalid_token` errors after connecting:** the host may have omitted
`resource=https://kabo.sh/mcp-pub` (RFC 8707) and obtained a token for another Kabo endpoint.
Disconnect and reconnect Kabo through the host so it uses
`https://kabo.sh/.well-known/oauth-protected-resource/mcp-pub`. Do not paste a token manually or alter
requests to bypass authentication. Tokens for `/mcp` or the removed `/mcp-directory` endpoint do not
work here. Once the corresponding server change is deployed, the 401 response's `authorization_hint`
and `WWW-Authenticate` error description provide the same guidance.

Each research run uses a separate local directory and saves complete MCP results. Do not reconstruct
truncated evidence or read the host's credentials or session logs. Follow the steps in each Skill.

**Claude Code is authorized interactively, but `claude -p` says authentication is needed:** an old
`plugin:kabo:kabo` entry in `~/.claude/mcp-needs-auth-cache.json` may still mark it as `needs-auth` with
zero tools, even when `claude mcp list` says Connected. Remove only that entry, or wait for it to expire,
then retry. This host behavior was observed in Claude Code 2.1.308 on October 22, 2026.

**Frame images:** frame reads also request `delivery: "download"`. The script saves images in the run
directory, and the host opens those local files. Claude Code may store inline images in its own
session directory, outside the exportable result. If an entire result has no ready download, export
the full inline result and use `stage`. If only some artifacts are unavailable or the script cannot
access the network, read those artifacts again without `delivery`, then export and stage them.
Skills do not read the host's session or `tool-results` directories.

## Updating, uninstalling and rollback

Moving from a development version to 1.0.0 resets the version numbering while keeping the latest
implementation; see [CHANGELOG](CHANGELOG.md) (Chinese). SemVer ranks `1.0.0` below the former `3.x`
versions, so do not rely on automatic upgrade detection. Refresh the marketplace, update the
installation, and confirm that the host loads `1.0.0` or later. If it retains the old version, uninstall the
plugin through the host and reinstall it. Keep research files and credentials intact. Host behavior
when moving to a lower version number still needs testing.

Normal update commands:

- Claude Code: `/plugin marketplace update kabo-mcp-plugin`, or
  `claude plugin update kabo@kabo-mcp-plugin` in the terminal.
- Codex: `codex plugin marketplace upgrade kabo-plugin-openai` for a GitHub source, or `git pull` for a
  local clone; then run `codex plugin add kabo@kabo-plugin-openai` again.

The connection and OAuth resource stay at `/mcp-pub` when moving from the old 3.0.1 or 3.1.x versions.
Renumbering alone does not change authorization. Older installations using `/mcp-directory` must
reauthorize for `/mcp-pub`. The current package has fifteen fixed Skills, downloads full evidence, and
stores raw tool results in each run's `host-results/` directory. The old dynamic menu, channel switch,
download cache and Node runner are no longer included. Validate in a separate workspace before
updating the existing `kabo` entry. Old research directories are not migrated or deleted automatically.

Do not roll back to 3.0.0 or earlier under the **old numbering scheme**, including the former 2.0.0
ZIP: their `/mcp-directory` connection no longer works. Fix current problems in a new release instead
of replacing signed content with an old package. Uninstall through the host to remove the plugin and
connection. Delete local research files only when the user asks.

## Repository layout

```text
plugin.json                     # Portable manifest for Codex / Cursor
mcp.json                        # Production MCP connection
.claude-plugin/plugin.json       # Claude Code metadata
.claude-plugin/marketplace.json  # Claude Code marketplace (not included in the ZIP)
.agents/plugins/marketplace.json # Codex marketplace (not included in the ZIP)
.mcp.json                       # Claude Code HTTP MCP configuration
skills/                         # Fifteen complete Skills and their resources
skills.lock.json                # Original Skill source and versions
runtime-contract.json           # Fixed tool and Skill inventory
CHANGELOG.md                    # Version history (Chinese)
```

The fifteen Skills cover video analysis, standard and deep account reviews, scripts and packaging,
content ideas, Instagram/TikTok/YouTube trends, creator/TikTok competitor research, reach-drop
diagnosis, publish-timing recommendations, platform monetization plans, official platform rule checks,
and `remake-viral-video`, which remakes a viral talking-head or voice-over video of 90 seconds or less
(talking videos may cut to screen recordings or show a product) with the creator in it. See
[skills.lock.json](skills.lock.json) for exact IDs and source versions. All hosts use the
same Skills.

`diagnose-reach-drop`, `recommend-publish-timing`, `plan-platform-monetization` and
`verify-platform-rule` are included in 1.0.9. Their complete cross-host acceptance is still pending;
Current release acceptance focuses on short-window content-language checks and user-approved cross-language searches.
`verify-platform-rule` needs the `official_page_render_page` tool on `/mcp-pub` (restored by kabo#1817).

`remake-viral-video` uses the `start_remake_video`, `get_remake_video` and `render_remake_video`
tools on `/mcp-pub`. Its scripts download the brief and the finished MP4 from the same signed
`storage.googleapis.com` links as evidence downloads; there is no `stage` fallback for them.

- The Kabo service, which is not part of this repository, owns MCP, OAuth, data tools, asynchronous
  jobs, the website and deployment.
- The Skills were adapted from the ones used by Kabo's web agent and are now maintained here.
- This repository owns the plugin Skills, offline checks, host metadata, reproducible packages and
  acceptance evidence.

## Development

Use Node 24 and Python 3. Normal builds do not read neighboring repositories or a Skill registry.

```sh
npm ci --ignore-scripts
npm run verify
```

Edit the bundled `skills/` here and submit changes for PR review. Do not overwrite them with a fresh
upstream export. The web agent's Skills are maintained separately.
`skills.lock.json` records the original source, not a byte-for-byte comparison; update it and
`runtime-contract.json` when adding or removing a Skill.

Follow the [Kabo Skills specification](docs/skills-spec.md) (Chinese). Run `npm run skills:lint` for
details. Enforcement remains disabled (`skills-spec.json` has an empty `enforced` list), and
`npm run verify` prints the summary. The MCP artifacts in `contracts/directory-mcp/` record the
provider commit and file hashes. New tools require an explicit update to those artifacts.

Build from a committed revision:

```sh
npm run build -- --commit <full-40-character-commit> --output <new-output-directory>
```

The build reads Git blobs from that commit and produces `kabo-<version>.zip`, its SHA-256, and a file
manifest. Every declared component is included. Development documentation, tests, dependencies and
marketplace listings are excluded. The repository's resource limits are 512 files and 5 MiB per Skill,
and 2,048 files and 64 MiB for the whole package; these are local build limits.

## Acceptance and release

Passing CI and building a ZIP establish local structure and test results. Production acceptance still
covers tool visibility (including YouTube), complete tasks and reports delivered once, photo/carousel/
video evidence, OAuth, incorrect audience/scope, account isolation, deletion, five positive and three
negative review cases, and a reviewer account. Without complete production evidence,
`submissionReady=false`.

Maintainer guides are in Chinese: [production acceptance](docs/acceptance.md) and
[submission materials](docs/submission.md).
Deployment, marketplace upload and Publish are separate actions.

[Privacy](https://kabo.sh/privacy) · [Terms](https://kabo.sh/terms)

Format references: [OpenAI plugins](https://developers.openai.com/plugins/build/plugins),
[Claude Code manifests](https://code.claude.com/docs/en/plugins-reference), and
[Cursor portable plugins](https://cursor.com/docs/plugins).

## Your saved Kabo work

When your Kabo deployment provides workspace tools, your agent can read your linked accounts,
saved creator profiles, official social connection status, scripts and video library after you
grant workspace access. `get_kabo_info` shows basic identity and permissions without credits.
Ask the agent to save a completed script or update an existing one; it keeps the saved version
and rejects conflicting edits. You can also ask to connect a public Instagram, TikTok or YouTube
account. Public Connect uses data allowance and does not start official social OAuth or analysis.
Connecting Kabo asks only for read access. The first time you save a script or connect an account,
your host asks you to approve write access. Existing connections may need authorization for the
new workspace permissions. If these tools are unavailable, the original public research and local
drafting workflows remain usable.

The three trends Skills can also start from your saved account profile. When you ask for trends for
your own linked account, the agent reads the account's saved content profile (your signature
content, content language and formats) with `get_creator_profile`, keeps the complete result as a
local file and passes it to the trends driver. The searches then start from your own seed words,
leave out your own posts and accounts far larger or smaller than yours, and share the
recommendations across your signature content. In the general trend-research Workflow, content
language steers the search words. Short-window recommendations (seven days or less) also check
candidate language before ranking. You can explicitly add other languages or allow any language
for the current request; that choice overrides the account default without editing your profile.
The Skills never write or edit the profile file. If the
profile is missing or empty, workspace access is not granted, or Kabo does not offer the profile
yet, the Skills run the same public trend scan as before.

You can also correct that saved profile in the conversation. Ask the trends Skills or the account
review to rename, remove or merge a signature content item, make one your main item, exclude a
topic, or set the language you make content in or your market, and the agent saves the edit to Kabo
with `update_content_preferences`. Your edits are kept per account and come back on every later
read, in this plugin and in Kabo's web app; the profile Kabo computed stays as it was, and you can
reset your edits. A plain, unambiguous change is saved and repeated back to you in one sentence; a
guess, a removal, a merge or a reset is restated for you to confirm first. Your market is only the
default search region (Instagram search takes none); it never filters results by region.
Content-language checks follow the short-window rules above. An edit applies from the next trend run. Saving needs write access, which your host asks
you to approve the first time; without it, or when Kabo does not offer the tool yet, nothing is
saved and the research runs as before.
