import subprocess
import sys
import time
from pathlib import Path

import ffmpeg

from ..cli.logging import Logger


def assemble(input_path: Path, encoded_audio_path: Path, output_path: Path, audio_idx: int, probe: dict, logger: Logger):
    if output_path.suffix.lower() == ".mkv":
        _assemble_mkvmerge(input_path, encoded_audio_path, output_path, audio_idx, probe, logger)
    else:
        _assemble_ffmpeg(input_path, encoded_audio_path, output_path, audio_idx, probe, logger)


def _assemble_ffmpeg(input_path: Path, encoded_audio_path: Path, output_path: Path, audio_idx: int, probe: dict, logger: Logger):
    """
    WHY: For non-MKV output (mp4, mov, etc.) ffmpeg is the only practical
    muxer.  Audio is pre-encoded to AAC in an m4a container, so the mux
    step uses c:a:0=copy — there is no encode-speed mismatch and therefore
    no muxing queue overflow risk.
    """
    t0 = time.perf_counter()
    try:
        probe_streams = probe.get("streams", [])
        orig = ffmpeg.input(str(input_path))
        proc = ffmpeg.input(str(encoded_audio_path))

        stream_objects = []
        codec_opts = {}
        v_count = a_count = s_count = 0

        for i, s in enumerate(probe_streams):
            if i == audio_idx:
                stream_objects.append(proc["a:0"])
                codec_opts[f"c:a:{a_count}"] = "copy"
                a_count += 1
            else:
                st = s["codec_type"]
                if st == "video":
                    stream_objects.append(orig[f"v:{v_count}"])
                    codec_opts[f"c:v:{v_count}"] = "copy"
                    v_count += 1
                elif st == "subtitle":
                    stream_objects.append(orig[f"s:{s_count}"])
                    codec_opts[f"c:s:{s_count}"] = "copy"
                    s_count += 1

        src_audio_type_idx = sum(1 for s in probe_streams[:audio_idx] if s["codec_type"] == "audio")

        # WHY: Although AAC is pre-encoded so the worst of the speed mismatch
        # is gone, ffmpeg's copy muxer can still back up on large sources with
        # many subtitle streams or complex container structures.  Keep the queue
        # raised as a safety net.
        codec_opts["max_muxing_queue_size"] = "4096"
        codec_opts["muxing_queue_data_threshold"] = "8388608"

        codec_opts["map_metadata"] = "0"
        codec_opts["map_metadata:s:a:0"] = f"0:s:a:{src_audio_type_idx}"
        if v_count:
            codec_opts["map_metadata:s:v:0"] = "0:s:v:0"
        for i in range(s_count):
            codec_opts[f"map_metadata:s:s:{i}"] = f"0:s:s:{i}"

        out_file = ffmpeg.output(*stream_objects, str(output_path), **codec_opts)
        out, err = ffmpeg.run(out_file, overwrite_output=True, capture_stdout=True, capture_stderr=True)
    except ffmpeg.Error as e:
        elapsed = time.perf_counter() - t0
        stderr_text = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        print(f"ffmpeg error:\n{stderr_text}", file=sys.stderr)
        logger.error("assemble_output_failed",
                     {"elapsed_sec": f"{elapsed:.3f}", "stderr": stderr_text},
                     "Output assembly failed")
        raise
    elapsed = time.perf_counter() - t0
    logger.info("assemble_output_complete", {"elapsed_sec": f"{elapsed:.3f}", "output": str(output_path)})
    sub_log = logger.workdir / "logs" / "assemble_output.log"
    with open(sub_log, "w") as f:
        if out:
            f.write(out.decode("utf-8", errors="replace"))
        if err:
            f.write(err.decode("utf-8", errors="replace"))


def _assemble_mkvmerge(input_path: Path, encoded_audio_path: Path, output_path: Path, audio_idx: int, probe: dict, logger: Logger):
    """
    WHY: mkvtoolnix's mkvmerge is purpose-built for Matroska assembly and
    handles timestamps, codec-private data, attachments, and chapters
    correctly where ffmpeg's Matroska muxer often fumbles.  By delegating
    MKV output to mkvmerge we eliminate the whole class of ffmpeg
    muxing-queue / container-integrity bugs for .mkv output.
    """
    t0 = time.perf_counter()
    try:
        orig_streams = probe.get("streams", [])
        orig_audio = orig_streams[audio_idx]
        tags = orig_audio.get("tags", {})
        disp = orig_audio.get("disposition", {})

        cmd = ["mkvmerge", "-o", str(output_path)]

        cmd += ["--no-audio", str(input_path)]

        lang = tags.get("language", "und")
        cmd += ["--language", f"0:{lang}"]
        if title := tags.get("title"):
            cmd += ["--track-name", f"0:{title}"]
        cmd += ["--default-track", f"0:{disp.get('default', 0)}"]
        cmd += ["--forced-track", f"0:{disp.get('forced', 0)}"]

        cmd += [
            "--no-video", "--no-subtitles", "--no-chapters",
            "--no-attachments", "--no-global-tags",
            str(encoded_audio_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        elapsed = time.perf_counter() - t0
        logger.info("mkvmerge_complete",
                    {"elapsed_sec": f"{elapsed:.3f}", "output": str(output_path)})
        sub_log = logger.workdir / "logs" / "mkvmerge.log"
        with open(sub_log, "w") as f:
            f.write(result.stdout)
            f.write(result.stderr)
    except subprocess.CalledProcessError as e:
        elapsed = time.perf_counter() - t0
        stderr_text = e.stderr or ""
        print(f"mkvmerge error:\n{stderr_text}", file=sys.stderr)
        logger.error("mkvmerge_failed",
                     {"elapsed_sec": f"{elapsed:.3f}", "stderr": stderr_text},
                     "mkvmerge assembly failed")
        raise