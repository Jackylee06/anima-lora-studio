"""GPU inference entry point executed inside the isolated caption environment.

The core packaged worker deliberately has no torch/onnxruntime dependency.  It
starts this script with the caption environment's Python and exchanges one JSON
record per asset on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SOURCE_ROOT = HERE.parent if (HERE.parent / "adapters").is_dir() else HERE
sys.path.insert(0, str(SOURCE_ROOT))

from adapters.joycaption import JoyCaptionAdapter  # noqa: E402
from adapters.wd14 import WD14Tagger  # noqa: E402


def emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("wd14", "joycaption"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--assets", required=True)
    arguments = parser.parse_args()
    config = json.loads(Path(arguments.config).read_text(encoding="utf-8"))
    assets = json.loads(Path(arguments.assets).read_text(encoding="utf-8"))

    adapter: Any = None
    try:
        if arguments.stage == "wd14":
            adapter = WD14Tagger(
                Path(config["modelPath"]), Path(config["tagsPath"]), config.get("providers")
            )
            infer = lambda path: adapter.tag(  # noqa: E731
                path, float(config["generalThreshold"]), float(config["characterThreshold"])
            )
        else:
            adapter = JoyCaptionAdapter(config["modelId"], config.get("cacheDir"), config["precision"], config.get("revision"))
            infer = lambda path: {  # noqa: E731
                "caption": adapter.caption(path, config["prompt"], int(config["maxTokens"])),
                "prompt": config["prompt"],
            }
        for asset in assets:
            try:
                emit({"assetId": asset["id"], "ok": True, "result": infer(Path(asset["path"]))})
            except Exception as error:  # one bad image must not abort the batch
                emit({"assetId": asset["id"], "ok": False, "error": str(error)})
        return 0
    finally:
        if adapter is not None:
            adapter.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        sys.stderr.write(f"{type(error).__name__}: {error}\n")
        raise
