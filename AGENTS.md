This is the `no-swear` app - a command-line tool to censor swear words from a selected audio track in a media file. It uses ffmpeg and a whisper model to replace swear words with non-swearing filler.

# General instructions

- NEVER glob or grep the whole repository. You must explore from shallow to deep and only glob, grep, or read small focused slices of the codebase. Be token efficient.
- `no-swear.spec.md` is a full declaration of behavior.
  - Technical details must only be included if they are non-negotiable constraints.
  - It must be in timeless, version-less language with no reference to past versions.
  - It must be standalone, not depend on other files unless specifically instructed to, and should in theory allow the app to be built from scratch.
  - It must never be rewritten - only surgical edits and tweaks for consistency are allowed.
  - It must NEVER be edited unless specifically instructed to.
- NEVER create abstractions unless they clearly make readability easier. Linear code describing an algorithm or process is preferred. Repeat yourself twice, factor out abstractions on the third use.
- NEVER factor out short, simple code that is near-impossible get wrong (this overrides the previous rule).
- Expect ffmpeg tools to be available; stop and display an error if you cannot run them.
- Use `data/swearing-clip.mkv` to test all changes; if not found, no hunting for media - just show user an error.
- System temp directory `/tmp` is BANNED. Use `scratch/` directory in the repo for all temporary files.
- ALWAYS run no-swear with `--verbose`.
- ddg MCP rules:
  - You MUST sleep for at least 5 seconds between each ddg search query or DuckDuckGo temporarily bans our public IP
  - Get more results rather than few per query (minimum 10) then use the ddg summary tool to find out if each link is worth WebFetching
- Lint: `uvx ruff check` or `uvx ruff check <FILE>`

# no-swear instructions

- It is a Python project managed with `uv`, runnable with `uvx`.
- `no-swear.spec.md` defines its behavior (can be partial if being iterated upon)
- Run in dev mode with `uv run no-swear`.
- `no-swear` MUST use tempfile package and NEVER manage temp files/dirs manually; trust the `tempfile` functions to choose the correct place.
  - Always set `TMPDIR` environment variable to `scratch/` in the project root - the `tempfile` functions can be trusted to then create files/directories in `scratch/`
