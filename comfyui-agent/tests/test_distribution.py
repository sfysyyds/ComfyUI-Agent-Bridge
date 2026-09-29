from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tomllib
import unittest


AGENT_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = AGENT_ROOT.parent
PLUGIN_ROOT = DIST_ROOT / "comfyui-plugin"
MCP_ROOT = AGENT_ROOT / "mcp_server"
sys.path.insert(0, str(MCP_ROOT / "src"))

from comfyui_agent_bridge_mcp import __version__  # noqa: E402


class DistributionTests(unittest.TestCase):
    def test_versions_are_consistent(self):
        project = tomllib.loads(
            (MCP_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (AGENT_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        state_source = (PLUGIN_ROOT / "bridge_state.py").read_text(encoding="utf-8")
        match = re.search(r'^PLUGIN_VERSION\s*=\s*"([^"]+)"', state_source, re.MULTILINE)
        self.assertIsNotNone(match)
        versions = {
            __version__,
            project["project"]["version"],
            manifest["version"],
            match.group(1),
        }
        self.assertEqual(versions, {"1.3.3"})

    def test_required_frontend_modules_exist(self):
        for name in (
            "agent_bridge.js",
            "agent_bridge_core.js",
            "agent_bridge_graph.js",
            "agent_bridge_highlight.js",
            "agent_bridge.css",
        ):
            self.assertTrue((PLUGIN_ROOT / "web" / name).is_file(), name)

    def test_skill_has_valid_frontmatter(self):
        text = (
            AGENT_ROOT / "skills" / "comfyui-live-control" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("\nname: comfyui-live-control\n", text)
        self.assertIn("\ndescription:", text)

    def test_release_tree_has_no_build_artifacts(self):
        forbidden = []
        for path in DIST_ROOT.rglob("*"):
            if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}:
                forbidden.append(path)
            if path.name == "build" or path.name.endswith(".egg-info"):
                forbidden.append(path)
        self.assertEqual(
            forbidden,
            [],
            "remove generated artifacts: "
            + ", ".join(str(path.relative_to(DIST_ROOT)) for path in forbidden),
        )


if __name__ == "__main__":
    unittest.main()
