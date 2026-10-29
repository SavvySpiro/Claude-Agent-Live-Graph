#!/usr/bin/env bash
# Start (or reuse) the agentgraph server in the background and open it in the browser.
#   ./agentgraph.sh --dir ~/some/reports [--summarize]
cd "$(dirname "$(readlink -f "$0")")" && exec python3 -m agentgraph --background --open "$@"
