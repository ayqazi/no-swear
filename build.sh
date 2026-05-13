#!/bin/bash

set -euo pipefail

[[ -f .env ]] && source .env

cargo build "$@"
