from __future__ import annotations

import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server" / "src"))

from comfyui_agent_bridge_mcp.workflow import (  # noqa: E402
    compact_schema,
    generation_result,
    workflow_outline,
)


SESSION = {
    "session_id": "s1",
    "workflow_id": "workflow-1",
    "title": "test.json",
    "revision": 7,
    "view": {
        "selection": [2],
        "viewport": {"x": 0, "y": 0, "width": 1000, "height": 600, "zoom": 1},
        "nodes": [
            {
                "id": 2,
                "type": "KSampler",
                "comfy_class": "KSampler",
                "title": "Sampler",
                "mode": 0,
                "pos": [300, 100],
                "widgets": [{"name": "steps", "value": 20}],
                "inputs": [{"name": "model", "type": "MODEL", "link": 10}],
                "outputs": [{"name": "LATENT", "type": "LATENT", "links": []}],
            },
            {
                "id": 1,
                "type": "CheckpointLoaderSimple",
                "comfy_class": "CheckpointLoaderSimple",
                "title": "Checkpoint",
                "mode": 0,
                "pos": [0, 100],
                "widgets": [{"name": "ckpt_name", "value": "model.safetensors"}],
                "inputs": [],
                "outputs": [{"name": "MODEL", "type": "MODEL", "links": [10]}],
            },
        ],
        "links": [[10, 1, 0, 2, 0, "MODEL"]],
    },
}


class WorkflowTests(unittest.TestCase):
    def test_outline_is_dependency_ordered(self):
        result = workflow_outline(SESSION)
        self.assertEqual(result["revision"], 7)
        self.assertLess(result["outline"].index("#1 "), result["outline"].index("#2 "))
        self.assertIn("model <- #1.MODEL", result["outline"])
        self.assertEqual([node["id"] for node in result["nodes"]], [1, 2])
        self.assertEqual(result["nodes"][1]["class_type"], "KSampler")

    def test_schema_compacts_large_enums(self):
        schema = {
            "input": {"required": {"lora_name": [[str(i) for i in range(250)], {}]}},
            "output": ["MODEL"],
            "output_name": ["MODEL"],
            "category": "loaders",
        }
        result = compact_schema("LoraLoader", schema)
        spec = result["inputs"]["required"]["lora_name"]
        self.assertEqual(spec["choice_count"], 250)
        self.assertEqual(len(spec["choices"]), 200)
        self.assertTrue(spec["choices_truncated"])

    def test_schema_preserves_outputs_when_names_are_shorter(self):
        result = compact_schema(
            "Example",
            {
                "input": {},
                "output": ["MODEL", "CLIP"],
                "output_name": ["model"],
            },
        )
        self.assertEqual(
            result["outputs"],
            [
                {"index": 0, "type": "MODEL", "name": "model"},
                {"index": 1, "type": "CLIP", "name": "CLIP"},
            ],
        )

    def test_generation_files_have_urls(self):
        result = generation_result(
            "p1",
            {
                "status": {"completed": True, "status_str": "success"},
                "outputs": {
                    "9": {
                        "images": [
                            {"filename": "x.png", "subfolder": "", "type": "output"}
                        ]
                    }
                },
            },
            lambda item: f"http://example/{item['filename']}",
        )
        self.assertTrue(result["completed"])
        self.assertEqual(result["files"][0]["url"], "http://example/x.png")


if __name__ == "__main__":
    unittest.main()
