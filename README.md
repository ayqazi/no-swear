# No Swear

Censor swear words from media with AI

## Licensing

Unless otherwise stated, all files in this repository are licensed under the GNU General Public License version 3.0 or later (GPL-3.0-or-later), as described in LICENSE.

The contents of Markdown (.md) files are additionally licensed under the Creative Commons Attribution-ShareAlike 4.0 International License (CC BY-SA 4.0), as described in LICENSE-CC-BY-SA-4.0.txt, at the recipient’s option.

Where conflicts arise, the license applicable to the file type governs its use.

## Whisper Models

The `--model` flag accepts any model name supported by [faster-whisper](https://github.com/SYSTRAN/faster-whisper):

| Model | Variants |
|---|---|
| `tiny` / `tiny.en` | Small, fast — recommended for quick tests |
| `base` / `base.en` | Slightly better accuracy |
| `small` / `small.en` | Good balance of speed and accuracy |
| `medium` / `medium.en` | Higher accuracy, slower |
| `large` / `large-v2` / `large-v3` | Most accurate, slowest |

Default: `tiny.en`
Recommended: `medium.en`
