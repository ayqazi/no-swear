This is the `no-swear` app - a command-line tool to censor swear words from a selected audio track in a media file. It uses ffmpeg, mkvmerge, and a whisper model to replace swear words with non-swearing filler.

General logic:
- extract audio stream, downmixed to and feed to faster-whisper to get censored-word timestamps
- convert audio stream to a PCM WAV (down-mixed to stereo) and use censored-word timestamps to brown-noise-out censored words in both channels
- convert censored audio stream to AAC
- combine back into output media container with ffmpeg or mkvmerge, including the censored audio stream as the only audio stream

Do not glob codebase; all relevant files are pyproject.toml and the Python code under src/no_swear/

# General instructions

- NEVER glob or grep the whole repository. You must explore from shallow to deep and only glob, grep, or read small focused slices of the codebase. Be token efficient.
- NEVER create abstractions unless they clearly make readability easier.
- Linear code describing an algorithm or process is preferred.
- Repeat yourself twice, factor out abstractions on the third use.
- NEVER factor out short, simple code that is near-impossible get wrong (this overrides the previous rule).
- Expect ffmpeg and mkvtoolnix tools to be available; stop and display an error if you cannot run them.
- System temp directory `/tmp` is BANNED. Use `scratch/` directory in the repo for all temporary and files. list/grep/glob or other tools will NOT find files there, you must use bash cli.
- All temporary or output files must have a 6 character alphanumeric component for disambiguiation.
- ddg MCP rules:
  - You MUST sleep for at least 5 seconds between each ddg search query or DuckDuckGo temporarily bans our public IP
  - Get more results rather than few per query (minimum 10) then use the ddg summary tool to find out if each link is worth WebFetching
- Lint: `uvx ruff check` or `uvx ruff check <FILE>`
- `rm` is disabled. You must only use `trash` with `-s` flag: `trash -s FILE_OR_DIR [FILE_OR_DIR...]`
- Only use POSIX commands
- NEVER use subagents without explicit permission

# no-swear instructions

- It is a Python project managed with `uv`.
- Run in dev mode with `uv run no-swear`.
- All code is in `no_swear` Python module, located at `src/no_swear` (`src` project layout).
- `no-swear` MUST use tempfile package and NEVER manage temp files/dirs manually; trust the `tempfile` functions to choose the correct place.
  - Always set `TMPDIR` environment variable to the absolute path of the `scratch/` directory - the `tempfile` module will create all temp files and dirs relative to it; this is TRUSTED behavior. Assume it works.
- Use `data/swearing-clip.mkv` (simple 5-minute clip for a quick test - audio stream index 1) and `data/jurassic-world.2025.15-clip.mkv` (15 minute 4k multi-audio clip final comprehensive test, audio stream index 2) to test all changes.
- You will use the following "fake" swear words for censorship: "jesus", "out", "all", "sergeant", "brother", "dinosaur" (6 words). No real swear words must appear in your context.

# Examples for managing media files

`agents/MEDIA_FILES.md`
