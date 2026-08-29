from pathlib import Path

from . import commands
from .cli.logging import Logger


def recombine(
    input_path: Path,
    encoded_audio_path: Path,
    output_path: Path,
    audio_idx: int,
    probe_result: dict,
    logger: Logger,
):
    commands.assemble(
        str(input_path),
        str(encoded_audio_path),
        str(output_path),
        audio_idx,
        probe_result,
        commands.LocalRunner(),
        logger,
    )
