#!/usr/bin/env python3
"""Fetch Pokemon data from PokeAPI and write it to ./poke_return.json."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


POKEAPI_BASE = "https://pokeapi.co/api/v2/pokemon"


def fetch_pokemon(name: str) -> dict:
    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("pokemon name cannot be empty")

    url = f"{POKEAPI_BASE}/{urllib.parse.quote(normalized)}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ugc-pipeline-mini-prototype/0.1",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)

    return {
        "requested_name": name,
        "api_url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "pokemon": {
            "id": payload.get("id"),
            "name": payload.get("name"),
            "height": payload.get("height"),
            "weight": payload.get("weight"),
            "base_experience": payload.get("base_experience"),
            "types": [
                item.get("type", {}).get("name")
                for item in payload.get("types", [])
                if item.get("type", {}).get("name")
            ],
            "abilities": [
                item.get("ability", {}).get("name")
                for item in payload.get("abilities", [])
                if item.get("ability", {}).get("name")
            ],
            "sprite_front_default": payload.get("sprites", {}).get("front_default"),
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: get_pokemon.py <pokemon-name>", file=sys.stderr)
        return 2

    output_path = Path.cwd() / "poke_return.json"

    try:
        result = fetch_pokemon(argv[1])
    except urllib.error.HTTPError as exc:
        print(f"pokeapi returned HTTP {exc.code} for {argv[1]!r}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"failed to fetch pokemon data: {exc}", file=sys.stderr)
        return 1

    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
