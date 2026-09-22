#!/usr/bin/env bash
# Render all Mermaid .mmd diagrams in docs/diagrams to PNG using mermaid-cli.
# Offline note: pull the image once (minlag/mermaid-cli) on a connected box,
# `docker save` it, and load it on the airgapped host to re-render if needed.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/docs/diagrams"
IMG="minlag/mermaid-cli:latest"

for f in "$DIR"/*.mmd; do
  name="$(basename "$f" .mmd)"
  echo "Rendering $name ..."
  docker run --rm -v "$DIR:/data" "$IMG" \
    -i "/data/$name.mmd" -o "/data/$name.png" -b white -s 2
done

echo "Done:"
ls -la "$DIR"/*.png
