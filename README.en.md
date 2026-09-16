<div align="center">
  <p><a href="README.md">Português</a> · <strong>English</strong></p>
  <img src="assets/brand-logo.png" alt="Engenheiro de Vídeo" width="104">
  <h1>GET B-ROLLS</h1>
  <p><strong>From an idea to the right shot for your edit.</strong></p>
  <p>Find supporting footage, preview the motion, and review every choice<br>before receiving the final clips with their sources.</p>
  <p><a href="#getting-started">Getting started</a> · <a href="#highlights">Highlights</a> · <a href="#documentation">Documentation</a> · <a href="docs/GUIDE.md#instalação">Full guide</a></p>
</div>

Get B-rolls is a skill for collecting the videos and images that support a line, illustrate an idea, or show the exact person, product, or event mentioned in a script. You describe what you need; the agent researches, prepares previews, and gathers the options into a storyboard for your review.

- **Choose with context.** Each shot can include the supplied narration, selection rationale, time range, creator, and original source.
- **See it before deciding.** GIFs and contact sheets help you evaluate action, framing, and on-screen text.
- **Receive an organized collection.** Final clips are delivered with a record of their origin, review decision, and conditions of use.

## How it works

The agent looks for the literal source of what you mention: the actual fact, person, product, news item, or screen. Stock-footage libraries are used only when you explicitly ask for stock. You do not need a complete script to request a single insert: simply explain what should appear.

A preview may download working media so you can see the motion. Final delivery requires a human decision and a record of the source's conditions of use. If the time range or context changes, the shot returns to review.

<p align="center"><img src="assets/flow.en.svg" alt="Skill map: from request to organized collection" width="700"></p>

### What it collects

<p align="center"><img src="assets/formats.en.svg" alt="Formats: video becomes an MP4 at 1080p of the approved range; local images are copied unchanged; page captures come out as PNG/JPG with provenance" width="700"></p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey?style=flat-square" alt="macOS and Windows">
  <img src="https://img.shields.io/badge/agents-Codex%20%7C%20Claude%20Code-orange?style=flat-square" alt="Codex and Claude Code">
  <img src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/node-22%2B-green?style=flat-square" alt="Node 22+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License">
  <img src="https://img.shields.io/badge/version-2.3.7-blue?style=flat-square" alt="Version 2.3.7">
</p>

## Getting started

### 1. Add the skill to your agent

The official repository is [engenheirodevideo/get-brolls](https://github.com/engenheirodevideo/get-brolls). Clone the source and copy the complete `get-brolls/` folder to **one** of the destinations below:

```sh
git clone https://github.com/engenheirodevideo/get-brolls.git
cd get-brolls
```

| Agent | Personal installation | Inside a project | Invoke with |
|---|---|---|---|
| Codex | `~/.agents/skills/get-brolls/` | `.agents/skills/get-brolls/` | `$get-brolls` |
| Claude Code | `~/.claude/skills/get-brolls/` | `.claude/skills/get-brolls/` | `/get-brolls` |

When copying a development folder, exclude `.venv/`, `.tools/`, caches, projects, and private files. `skills/` and `.claude-plugin/` are Claude Code plugin artifacts and can be omitted when copying to Codex. Install dependencies in the final destination and open a new agent session. [See installation, updates, and compatibility.](docs/GUIDE.md#instalação)

#### Install as a Claude Code plugin

In Claude Code, you can also install the skill as a plugin, without cloning manually:

```text
/plugin marketplace add engenheirodevideo/get-brolls
/plugin install get-brolls@engenheirodevideo
```

Then run `/get-brolls-setup` in the session: the command runs the installer inside the plugin folder — `~/.claude/plugins/cache/engenheirodevideo/get-brolls/<version>/` — and reports the `doctor` verdict. You can also follow step 2 manually in that folder. Repeat the setup after each `/plugin update`. For Codex, the full-folder clone described above remains the way to install.

The skill triggers from the context of your request ("collect b-roll for this video"); the explicit form is `/get-brolls:get-brolls`, and setup is `/get-brolls-setup`. Do not confuse it with generic download skills: this one is the complete pipeline, with human review and a recorded license.

### 2. Prepare the environment

Requirements: Python 3.11+, FFmpeg/ffprobe, Node 22+, npm/npx, and curl. On macOS with Homebrew, start with `brew install python ffmpeg node`. On Windows, install the official versions and confirm that the executables are available on `PATH`. The [installation guide](docs/GUIDE.md#instalação) covers both platforms in full.

Run the installer for your operating system from the installed skill folder.

macOS:

```sh
bash scripts/install.sh --check
bash scripts/install.sh
python3 scripts/gb.py doctor
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install.ps1 -Check
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
python scripts/gb.py doctor
```

The installer creates local environments and obtains yt-dlp/EJS and Playwright CLI. `doctor` checks tool availability; access to each source depends on the URL and, when required, your browser session.

**YouTube works without an API key.** Pexels and Pixabay use their own optional keys, configured in the environment or in the skill's private `.env` file. Available settings are documented in [.env.example](.env.example).

### 3. Make your first request

In Codex:

```text
$get-brolls I need three inserts for a video about the Artemis launch.
Find real footage of the rocket and liftoff, using clips between 3 and 5 seconds.
Prepare the previews and a storyboard for me to review.
Use /path/to/my-video to store the project.
```

In Claude Code, replace the first invocation with `/get-brolls`. Replace the folder with the real path to your project, outside the skill installation. You may also provide a specific URL or a local file.

### First B-roll in 5 minutes

The shortest command-line sequence, using a keyless source (NASA). Replace `/path/to/my-video` with your project and `<ID>` with the identifier returned by the search — keep the quotes, because identifiers may contain spaces.

```sh
python3 scripts/gb.py search --provider nasa --query "Artemis launch" --limit 3 --intent literal --project /path/to/my-video
python3 scripts/gb.py preview --candidate "<ID>" --start 0 --end 4 --project /path/to/my-video
python3 scripts/gb.py review --project /path/to/my-video
python3 -m http.server 8767 --bind 127.0.0.1 --directory /path/to/my-video/brolls
```

Open [the local storyboard](http://127.0.0.1:8767/review.html), decide on the shots, and export the JSON. Then, from another terminal:

```sh
python3 scripts/gb.py import-review --file /path/to/review.json --by "Your name" --project /path/to/my-video
python3 scripts/gb.py permit --candidate "<ID>" --evidence "Real conditions of use for this source" --project /path/to/my-video
python3 scripts/gb.py fetch --candidate "<ID>" --project /path/to/my-video
python3 scripts/gb.py verify --project /path/to/my-video
```

At the end, `verify` answers `"count": 1` and the approved clip is in `/path/to/my-video/brolls/clips/`, with origin, creator, and decision recorded in `brolls/credits.md`. Replacing `nasa` with `commons` follows the same flow.

## Highlights

- **Local storyboard.** `review` generates `brolls/review.html`: a page to switch between a still image and a GIF, see the narration, time range, selection rationale, creator, and source, and approve, request an adjustment, or suggest another source per shot. [Storyboard details.](#storyboard)
- **Six sources covered.** YouTube and TikTok without an API key via yt-dlp/FFmpeg, Instagram through the authorized browser with an included video/audio pair collector, Pexels and Pixabay with their own keys, Wikimedia Commons and NASA without a key, and local file import. [See sources and transports.](#sources)
- **Project state at any moment.** `status --project` summarizes candidates, previews, decisions, permissions, and deliveries, with the suggested next step, without changing the project. [See command-line usage.](#command-line-usage)
- **Provenance record.** Every delivered shot carries source, creator, time range, and conditions of use — editorial approval is always yours.
- **Protected network access.** The collector accepts only public HTTPS URLs without credentials, rejects hostnames that resolve to local networks, and does not follow redirects.
- **Native on macOS and Windows.** Dedicated installers for both systems; the Bash YouTube helpers are optional.
- **Installable as a Claude Code plugin.** The repository itself is its own plugin marketplace, with `/get-brolls-setup` configuring the plugin folder and a mirrored skill that resolves paths via `${CLAUDE_PLUGIN_ROOT}`. The clone-as-skill flow remains identical for Codex.

## Storyboard

The `review` command generates `brolls/review.html`: a local page where you can evaluate the collection, move between shots, and send decisions back to the agent.

| In the review | What you can do |
|---|---|
| Selected shot | Switch between a still image and a GIF while preserving the original aspect ratio. |
| Context and origin | Inspect the supplied narration, time range, selection rationale, creator, and source link. |
| Decision per shot | Approve it, request an adjustment with a comment, or suggest a different source. |
| Export review | Save a JSON file for the agent to import into the project. |
| Print / PDF | Generate a static version with frames, sources, and comments. |

The gallery remains static; animation runs only in the selected shot and respects reduced-motion preferences. An optional screenshot of the speaker provides context and remains static. To evaluate a finished composition using the same insert, set `GB_GIF_SCOPE=full` and provide `--full-preview-file`.

Share the complete **`brolls/` folder** so that its images and GIFs remain accessible. To continue editing or regenerate previews, also preserve the originals and `.getbrolls-sources/`. [Review details.](docs/GUIDE.md#storyboard)

## Sources

| Source | How to find it | How it is obtained |
|---|---|---|
| **YouTube** | Keyword search or URL | yt-dlp + FFmpeg; no API key. |
| **Instagram** | Reel found in the browser | Captures video and audio from the same Reel; the included collector joins both streams. |
| **TikTok** | Complete URL discovered in the browser | yt-dlp + FFmpeg; no API key. |
| **Pexels / Pixabay** | Search their stock APIs | Provider-specific key; HTTPS download. |
| **Wikimedia Commons / NASA** | Search public APIs | HTTPS download; no key. |
| **Local file** | Supplied video, image, or screenshot | Local import with origin and creator when provided. |

Pexels and Pixabay are an optional route: the agent turns to stock libraries only when you explicitly ask for stock. The default is the literal source of what the narration cites.

For Instagram, the agent operates the authorized browser and passes both streams to the collector; the script does not capture the session by itself. The [Instagram guide](docs/GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4) covers stream pairing, download, audio, and recovery. Instagram and TikTok depend on URL discovery in the browser; the CLI does not implement global keyword search for those platforms.

The collector accepts only public HTTPS URLs without credentials, rejects hostnames that resolve to local networks, pins downloads to the validated address, and does not follow redirects. Files declared through `output=` must remain inside `--config-output-root`; batch outputs stay in the selected directory, and existing files are never overwritten.

Recorded trials include real acquisition from YouTube, Instagram, TikTok, Pexels, and Pixabay. For Commons and NASA, the evidence covers search and file availability without downloading the complete asset during that trial. See the results and their limitations in [Quality and evidence](docs/QUALITY.md).

## Command-line usage

Run the examples below from the skill folder. Replace `/path/to/my-video` with your project folder and `<ID>` with the identifier returned by the search — keep the quotes, because identifiers may contain spaces.

```sh
python3 scripts/gb.py status --project /path/to/my-video
python3 scripts/gb.py rules --project /path/to/my-video
python3 scripts/gb.py references --project /path/to/my-video
python3 scripts/gb.py search --provider youtube --query "NASA Artemis launch" --limit 3 --intent literal --project /path/to/my-video
python3 scripts/gb.py preview --candidate "<ID>" --start 0 --end 5 --reason "Show the liftoff mentioned in the video" --project /path/to/my-video
python3 scripts/gb.py review --project /path/to/my-video
python3 -m http.server 8767 --bind 127.0.0.1 --directory /path/to/my-video/brolls
```

Open [the local storyboard](http://127.0.0.1:8767/review.html), review the shots, and export your decisions. Then, from another terminal in the skill folder:

```sh
python3 scripts/gb.py import-review --file /path/to/review.json --by "Reviewer's name" --project /path/to/my-video
python3 scripts/gb.py permit --candidate "<ID>" --evidence "Real evidence of the conditions of use" --project /path/to/my-video
python3 scripts/gb.py fetch --candidate "<ID>" --project /path/to/my-video
python3 scripts/gb.py verify --project /path/to/my-video
```

Replace the name, exported file, and evidence with real data. Repeat `permit` and `fetch` for every approved candidate. `approve` can also record an explicit decision you have already received. `verify` checks file integrity and decoding; the editorial judgment remains yours.

`status` answers where the collection stands at any moment — candidates, previews, decisions, permissions, and deliveries, with the suggested next step — and never changes the project. Flow commands also return a `summary` field with a one-line account of what just happened. [Project state and progress.](docs/GUIDE.md#estado-do-projeto-e-progresso)

<details>
<summary>URLs, local files, and settings</summary>

- Specific URL: `resolve --url REAL_URL --shot insert-01 --project /path/to/my-video`.
- Local file: `resolve --file /path/to/original.mp4 --source-url REAL_URL --creator "Creator" --shot insert-01 --project /path/to/my-video`. Use real metadata; a source without a URL may omit `--source-url`.
- Exact narration: add `--narration` to the preview when a script has been supplied.
- Project rules: `init-rules --project /path/to/my-video` creates an editable [RULES.md](docs/RULES.md) with formats, preferred sources, and blocks.
- Choice memory: `remember` records approved or rejected references; `references` reads the history for that project.
- Images and news: import the file or screenshot with its provenance. See [media types](docs/GUIDE.md#tipos-de-assets-e-formatos) and [browser captures](docs/GUIDE.md#captura-de-notícias-e-páginas-pelo-navegador).
- Default GIF: 360 px, 8 fps, 128 colors, up to 10 seconds and 5 MB. A time range beyond the duration limit is rejected; an oversized result falls back to a still preview with a warning. `GB_PREVIEW_MODE=static` uses a poster and contact sheet.
- `--reference-only` prepares a static reference without obtaining remote media; a poster alone does not prove motion.
- The utilities in `scripts/getbrolls/tools/youtube/` provide YouTube search, frames, clips, and verification by `VIDEO_ID`. Their output must be imported through the CLI to become part of the project's record and review.

Use `python3 scripts/gb.py --help` and `python3 scripts/gb.py preview --help` to inspect arguments. Outside the skill folder, use the absolute path to `scripts/gb.py`.

</details>

When upgrading to 2.3.5, regenerate the Storyboard and export a current review. JSON files based on superseded decisions or missing `reviewEpoch` are rejected. [Migration guide (Portuguese).](docs/GUIDE.md#migração-para-235)

## Limits and privacy

Project files and imported originals remain local. Remote search and acquisition connect to the selected providers. Keep API keys, browser sessions, Instagram configs, and signed URLs in a private environment; that data does not belong in the distributed skill folder.

The storyboard is intended for trusted local projects and does not authenticate reviewers. Share only the material required for review. Conditions of use belong to each source; recording a decision does not automatically verify its license. Responsibility for the conditions of use of the material lies with whoever produces the video; the skill answers for source fidelity and for recording the provenance of every asset.

Final resolution depends on the source: prefer 1080p when available and inspect the actual dimensions. The tool preserves aspect ratio and reports format mismatches. It does not automatically assemble the complete video, perform image search through an API, or deliver isolated audio as a final asset.

Run one command per project at a time. Preserve originals, cache, and event history. If the record is saved but page generation fails, run `review` again. The CLI and installer are native on macOS and Windows; the Bash YouTube helpers are optional and have equivalents in the main CLI workflow. See [compatibility](docs/GUIDE.md#compatibilidade).

## Documentation

| Entry | Purpose |
|---|---|
| [README.md](README.md) | Product overview and first use in Portuguese. |
| **[README.en.md](README.en.md)** | Product overview and first use in English (this file). |
| [AGENTS.md](AGENTS.md) | Index for agents and maintainers: repository map, per-agent installation, and maintenance rules. |
| [docs/GUIDE.md](docs/GUIDE.md) · [SKILL.md](SKILL.md) | Complete operating guide and agent execution instructions. |
| [docs/QUALITY.md](docs/QUALITY.md) | Tests, real-world evidence, and known limitations. |
| [docs/RULES.md](docs/RULES.md) · [.env.example](.env.example) | Editorial rules and configuration options. |
| [docs/SECURITY.md](docs/SECURITY.md) | Handling of private data and vulnerability reporting. |
| `scripts/getbrolls/` | Single core: CLI, providers, Storyboard, Instagram collector, and YouTube utilities. |
| `assets/` | Logo, styles, and scripts used by the generated Storyboard. |
| `agents/` · `schemas/` | Agent presentation and data contract. |
| `tests/` · `.github/workflows/` | Tests and quality automation. |

To maintain the project, start with [CONTRIBUTING](CONTRIBUTING.md) and [AGENTS](AGENTS.md). See [QUALITY](docs/QUALITY.md), [CHANGELOG](CHANGELOG.md), and [SECURITY](docs/SECURITY.md) for evidence, changes, and handling of private data. The cloned repository (or the plugin installation) is the official source of the deliverable.

## Author

Created and maintained by **Bruno Moreira — Engenheiro de Vídeo**.  
Instagram: **[@zbrunomoreira](https://www.instagram.com/zbrunomoreira/)**.

Code is available under the [MIT License](LICENSE). External dependencies retain their own terms, listed in [Third-party notices](THIRD_PARTY_NOTICES.md).
