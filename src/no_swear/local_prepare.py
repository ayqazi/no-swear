import tempfile
from pathlib import Path

from . import commands
from .cli.logging import Logger


def prepare(
    input_value: str,
    output_value: str,
    audio_idx: int,
    workdir: Path,
    logger: Logger,
) -> tuple[Path, dict]:
    input_path = Path(input_value)
    if not input_path.is_file():
        raise SystemExit("input not found")

    output_path = Path(output_value)
    if not output_path.parent.is_dir():
        raise SystemExit("output dir invalid")

    runner = commands.LocalRunner()
    probe_result = commands.probe(str(input_path), runner, logger)
    commands.validate_audio_stream(probe_result, audio_idx)

    with tempfile.NamedTemporaryFile(
        dir=workdir, prefix="no_swear_", suffix=".mka", delete=False
    ) as temporary_file:
        audio_path = Path(temporary_file.name)
    commands.extract_audio(audio_idx, str(input_path), str(audio_path), runner, logger)
    return audio_path, probe_result
