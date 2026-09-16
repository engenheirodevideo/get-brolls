<div align="center">
  <p><a href="README.md">Português</a> · <strong>English</strong></p>
  <img src="assets/brand-logo.png" alt="Engenheiro de Vídeo" width="104">
  <h1>GET B-ROLLS</h1>
  <p><strong>From an idea to the right shot for your edit.</strong></p>
  <p>Find supporting footage, preview the motion, and review every choice<br>before receiving the final clips with their sources.</p>
  <p>v2.3.4 · Codex and Claude Code · macOS and Windows</p>
  <p><a href="#getting-started">Getting started</a> · <a href="#storyboard">Storyboard</a> · <a href="#sources">Sources</a> · <a href="GUIDE.md#instalação">Full guide</a></p>
</div>

Get B-rolls is a skill for collecting the videos and images that support a line, illustrate an idea, or show the exact person, product, or event mentioned in a script. You describe what you need; the agent researches, prepares previews, and gathers the options into a storyboard for your review.

- **Choose with context.** Each shot can include the supplied narration, selection rationale, time range, creator, and original source.
- **See it before deciding.** GIFs and contact sheets help you evaluate action, framing, and on-screen text.
- **Receive an organized collection.** Final clips are delivered with a record of their origin, review decision, and conditions of use.

## How it works

```text
Your script or request → Research → Previews → Your review → Final clips
```

The agent prioritizes literal sources when you mention a real entity. For illustrative ideas, it can also search stock-footage libraries. You do not need a complete script to request a single insert: simply explain what should appear.

A preview may download working media so you can see the motion. Final delivery requires a human decision and a record of the source's conditions of use. If the time range or context changes, the shot returns to review.

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

When copying a development folder, exclude `.venv/`, `.tools/`, caches, projects, and private files. Install dependencies in the final destination and open a new agent session. [See installation, updates, and compatibility.](GUIDE.md#instalação)

### 2. Prepare the environment

Requirements: Python 3.11+, FFmpeg/ffprobe, Node 22+, npm/npx, and curl. On macOS with Homebrew, start with `brew install python ffmpeg node`. On Windows, install the official versions and confirm that the executables are available on `PATH`. The [installation guide](GUIDE.md#instalação) covers both platforms in full.

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

Share the complete **`brolls/` folder** so that its images and GIFs remain accessible. To continue editing or regenerate previews, also preserve the originals and `.getbrolls-sources/`. [Review details.](GUIDE.md#storyboard)

## Sources

| Source | How to find it | How it is obtained |
|---|---|---|
| **YouTube** | Keyword search or URL | yt-dlp + FFmpeg; no API key. |
| **Instagram** | Reel found in the browser | Captures video and audio from the same Reel; the included collector joins both streams. |
| **TikTok** | Complete URL discovered in the browser | yt-dlp + FFmpeg; no API key. |
| **Pexels / Pixabay** | Search their stock APIs | Provider-specific key; HTTPS download. |
| **Wikimedia Commons / NASA** | Search public APIs | HTTPS download; no key. |
| **Local file** | Supplied video, image, or screenshot | Local import with origin and creator when provided. |

For Instagram, the agent operates the authorized browser and passes both streams to the collector; the script does not capture the session by itself. The [Instagram guide](GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4) covers stream pairing, download, audio, and recovery. Instagram and TikTok depend on URL discovery in the browser; the CLI does not implement global keyword search for those platforms.

The collector accepts only public HTTPS URLs without credentials, rejects hostnames that resolve to local networks, pins downloads to the validated address, and does not follow redirects. Files declared through `output=` must remain inside `--config-output-root`; batch outputs stay in the selected directory, and existing files are never overwritten.

Recorded trials include real acquisition from YouTube, Instagram, TikTok, Pexels, and Pixabay. For Commons and NASA, the evidence covers search and file availability without downloading the complete asset during that trial. See the results and their limitations in [Quality and evidence](QUALITY.md).

## Command-line usage

Run the examples below from the skill folder. Replace `/path/to/my-video` with your project folder and `ID` with the identifier returned by the search.

```sh
python3 scripts/gb.py rules --project /path/to/my-video
python3 scripts/gb.py references --project /path/to/my-video
python3 scripts/gb.py search --provider youtube --query "NASA Artemis launch" --limit 3 --intent literal --project /path/to/my-video
python3 scripts/gb.py preview --candidate ID --start 0 --end 5 --reason "Show the liftoff mentioned in the video" --project /path/to/my-video
python3 scripts/gb.py review --project /path/to/my-video
python3 -m http.server 8767 --bind 127.0.0.1 --directory /path/to/my-video/brolls
```

Open [the local storyboard](http://127.0.0.1:8767/review.html), review the shots, and export your decisions. Then, from another terminal in the skill folder:

```sh
python3 scripts/gb.py import-review --file /path/to/review.json --by "Reviewer's name" --project /path/to/my-video
python3 scripts/gb.py permit --candidate ID --evidence "Real evidence of the conditions of use" --project /path/to/my-video
python3 scripts/gb.py fetch --candidate ID --project /path/to/my-video
python3 scripts/gb.py verify --project /path/to/my-video
```

Replace the name, exported file, and evidence with real data. Repeat `permit` and `fetch` for every approved candidate. `approve` can also record an explicit decision you have already received. `verify` checks file integrity and decoding; the editorial judgment remains yours.

<details>
<summary>URLs, local files, and settings</summary>

- Specific URL: `resolve --url REAL_URL --shot insert-01 --project /path/to/my-video`.
- Local file: `resolve --file /path/to/original.mp4 --source-url REAL_URL --creator "Creator" --shot insert-01 --project /path/to/my-video`. Use real metadata; a source without a URL may omit `--source-url`.
- Exact narration: add `--narration` to the preview when a script has been supplied.
- Project rules: `init-rules --project /path/to/my-video` creates an editable [RULES.md](RULES.md) with formats, preferred sources, and blocks.
- Choice memory: `remember` records approved or rejected references; `references` reads the history for that project.
- Images and news: import the file or screenshot with its provenance. See [media types](GUIDE.md#tipos-de-assets-e-formatos) and [browser captures](GUIDE.md#captura-de-notícias-e-páginas-pelo-navegador).
- Default GIF: 360 px, 8 fps, 128 colors, up to 10 seconds and 5 MB. A time range beyond the duration limit is rejected; an oversized result falls back to a still preview with a warning. `GB_PREVIEW_MODE=static` uses a poster and contact sheet.
- `--reference-only` prepares a static reference without obtaining remote media; a poster alone does not prove motion.
- The utilities in `scripts/getbrolls/tools/youtube/` provide YouTube search, frames, clips, and verification by `VIDEO_ID`. Their output must be imported through the CLI to become part of the project's record and review.

Use `python3 scripts/gb.py --help` and `python3 scripts/gb.py preview --help` to inspect arguments. Outside the skill folder, use the absolute path to `scripts/gb.py`.

</details>

## Limits and privacy

Project files and imported originals remain local. Remote search and acquisition connect to the selected providers. Keep API keys, browser sessions, Instagram configs, and signed URLs in a private environment; that data does not belong in the distributed skill folder.

The storyboard is intended for trusted local projects and does not authenticate reviewers. Share only the material required for review. Conditions of use belong to each source; recording a decision does not automatically verify its license.

Final resolution depends on the source: prefer 1080p when available and inspect the actual dimensions. The tool preserves aspect ratio and reports format mismatches. It does not automatically assemble the complete video, perform image search through an API, or deliver isolated audio as a final asset.

Run one command per project at a time. Preserve originals, cache, and event history. If the record is saved but page generation fails, run `review` again. The CLI and installer are native on macOS and Windows; the Bash YouTube helpers are optional and have equivalents in the main CLI workflow. See [compatibility](GUIDE.md#compatibilidade).

## Inside the repository

| Entry | Purpose |
|---|---|
| [README.md](README.md) | Product overview and first use in Portuguese. |
| **[README.en.md](README.en.md)** | Product overview and first use in English. |
| [GUIDE.md](GUIDE.md) · [SKILL.md](SKILL.md) | Complete operating guide and agent execution instructions. |
| [QUALITY.md](QUALITY.md) | Tests, real-world evidence, and known limitations. |
| [RULES.md](RULES.md) · [.env.example](.env.example) | Editorial rules and configuration options. |
| `scripts/getbrolls/` | Single core: CLI, providers, Storyboard, Instagram collector, and YouTube utilities. |
| `assets/` | Logo, styles, and scripts used by the generated Storyboard. |
| `agents/` · `schemas/` | Agent presentation and data contract. |
| `tests/` · `.github/workflows/` | Tests and quality automation. |

To maintain the project, start with [CONTRIBUTING](CONTRIBUTING.md) and [AGENTS](AGENTS.md). See [QUALITY](QUALITY.md), [CHANGELOG](CHANGELOG.md), and [SECURITY](SECURITY.md) for evidence, changes, and handling of private data. The cloned repository is the official source of the deliverable.

## Author

Created and maintained by **Bruno Moreira — Engenheiro de Vídeo**.  
Instagram: **[@zbrunomoreira](https://www.instagram.com/zbrunomoreira/)**.

Code is available under the [MIT License](LICENSE). External dependencies retain their own terms, listed in [Third-party notices](THIRD_PARTY_NOTICES.md).
