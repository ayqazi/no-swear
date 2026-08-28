# Remote Mode Handoff Plan

## Purpose

Add optional SSH remote-file support without changing the existing command-line
workflow beyond one flag:

```text
--remote-host ssh://USER@HOST:PORT
```

The flag selects the mode for both positional paths. It must not be possible to
mix a local source with a remote target, or a remote source with a local target.

This document records the decisions and constraints for remote mode.

## Confirmed Remote Environment

The test host `doshbot@deimos.usbx.me` was accessed successfully with agent-only
authentication and no prompt when SSH and SCP were run in batch mode.

Remote operations use the following working directory for the configured account:

```text
/home16/doshbot
```

Relative and absolute paths were both accepted by SSH and SCP. The remote host
has the required media tools available:

- `ffmpeg`
- `ffprobe`
- `mkvmerge`

## Mode Selection

### Local mode

When `--remote-host` is absent:

- The input path is local.
- The output path is local.
- Local filesystem validation applies to both paths.
- The existing local processing flow remains unchanged.

### Remote mode

When `--remote-host` is present:

- The input path is remote.
- The output path is remote.
- The paths are passed to the remote tools as supplied.
- Relative paths resolve from the SSH/SCP working directory.
- Absolute paths remain absolute remote paths.
- No local home-directory lookup or path prefixing is performed.

The remote working directory is trusted to be the normal directory reached by
the configured account. The observed SSH and SCP behavior supports this model.

## Remote Host Validation

The host value must use the stated form:

```text
ssh://USER@HOST:PORT
```

Validation should reject malformed values before any media work starts. SSH and
SCP must use the user, host, and port from this value while retaining the user's
normal SSH configuration and agent setup.

Remote mode must operate non-interactively. If the user's SSH agent, key, host
verification, or SSH configuration is not already prepared, an SSH or SCP
operation should fail rather than prompting for a passphrase, password, or key.

## Single Remote Preflight

Remote command availability is checked exactly once when remote mode is
enabled.

The preflight occurs after argument parsing and mode selection, immediately
before remote work begins. It must happen before remote probing, transfers, or
media commands.

The preflight checks that the remote media toolchain is available:

- `ffmpeg`
- `ffprobe`
- `mkvmerge`

After this one successful check, command availability is not checked again at
individual pipeline stages. Later operations simply invoke the selected
external command and allow it to report its own failure.

Path and selected-stream validation may happen as part of the same initial
remote setup, but it must not turn into repeated command discovery.

Remote mode follows the existing tool's logging and error-reporting convention,
except that intermediate files are not present in the local work directory.

## Data Flow

The remote server is used only for container-level operations and file access.

1. Inspect the remote source and selected stream.
2. Demux the selected audio remotely without re-encoding into a hidden, randomly
   named remote temporary file beside the remote source.
3. Transfer the temporary audio file to the local working directory with SCP.
4. Delete the remote extraction temporary file.
5. Perform transcription, timestamp matching, PCM conversion, noise replacement,
   and AAC encoding locally.
6. Transfer the locally encoded censored audio with SCP to a hidden, randomly
   named remote temporary file beside the requested remote output.
7. Combine the remote source with the replacement audio remotely.
8. Delete the remote replacement temporary file.

Remote temporary files are deleted through SSH after their associated transfer
or media command completes or fails. Cleanup is performed for every temporary
file before the operation reports its failure.

The final remote combination is a remux, not another audio encode. MKV output
uses MKVToolNix; other supported output types use FFmpeg. The replacement audio
is already encoded locally and is copied into the final container.

## Temporary Remote Files

The remote extraction temporary file is created in the directory containing the
remote input. The remote replacement temporary file is created in the directory
containing the requested remote output. Both files are hidden and randomly named,
with a six-character alphanumeric component and the appropriate extension, for
example:

```text
.no-swear.A1b2C3.mka
.no-swear.Xy91Qp.m4a
```

The temporary files are working artifacts, not user-facing outputs. They are
deleted after their associated operation completes or fails.

## Remote Output Behavior

The requested output path is remote and should be written by the remote final
combination step. No local copy of the complete source or final media file is
required.

The existing overwrite behavior should be preserved. A failed external command
should expose its own error and should not trigger output rollback behavior. A
failed remote assembly may leave a partially written output at the requested path.

## Scope Limits

In scope:

- One optional `--remote-host` flag.
- Agent-backed, non-interactive SSH/SCP access.
- Remote input and remote output in the same invocation.
- Local audio analysis and censoring.
- Remote audio demux and final media remux.
- Cleanup of hidden remote temporary files after every associated operation.

Out of scope:

- Mixed local/remote paths.
- Remote speech recognition or audio processing.
- Remote audio encoding.
- Password, passphrase, or key provisioning.
- Automatic SSH configuration.
- A second user-facing path-base option.
