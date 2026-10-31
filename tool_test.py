#!/usr/bin/env python3
"""Test voice-driven app tool selection locally without Android or a camera.

Configuration changes are made only to a temporary copy of the JSON file.
Camera results are simulated and contain no image.
"""
import atexit
import json
from pathlib import Path
import tempfile

import configuration_agent
from configuration_tools import ConfigurationTools


class SimulatedAppTools(ConfigurationTools):
    def __init__(self, path, camera_index=0):
        source = Path(path)
        with source.open(encoding="utf-8") as f:
            initial = json.load(f)
        if not isinstance(initial, dict):
            raise ValueError("Configuration root must be a JSON object")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="voice-tool-test-")
        atexit.register(self.temp_dir.cleanup)
        temp_config = Path(self.temp_dir.name) / "settings.json"
        temp_config.write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")
        super().__init__(temp_config, camera_index)
        print("Tool test: configuration changes are temporary; camera output is simulated.", flush=True)

    def get_image(self):
        return {"ok": True, "simulated": True,
                "message": "This is a simulated camera call. No image was captured; the camera view is unknown."}


def main():
    # Use the production agent's model, speech, tool declarations and logging;
    # replace only the app-side tool implementations.
    configuration_agent.ConfigurationTools = SimulatedAppTools
    configuration_agent.main()


if __name__ == "__main__":
    main()
