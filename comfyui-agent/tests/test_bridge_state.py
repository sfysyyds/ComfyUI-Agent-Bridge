from __future__ import annotations

import sys
import time
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT.parent / "comfyui-plugin"
sys.path.insert(0, str(PLUGIN))

from bridge_state import (  # noqa: E402
    BridgeState,
    RevisionConflict,
    SessionAmbiguous,
    validate_undo_payload,
)


def snapshot(session_id: str = "tab-a", client_id: str = "client-a", value: int = 1):
    return {
        "session_id": session_id,
        "client_id": client_id,
        "workflow_id": "workflow-a",
        "activity_at_ms": 1000,
        "title": "demo.json",
        "workflow": {
            "last_node_id": 1,
            "nodes": [{"id": 1, "type": "Example", "widgets_values": [value]}],
            "links": [],
        },
        "view": {
            "nodes": [{"id": 1, "type": "Example", "widgets": [{"name": "value", "value": value}]}],
            "links": [],
        },
        "visible": True,
        "focused": True,
        "origin": "user",
    }


class BridgeStateTests(unittest.TestCase):
    def test_revision_changes_only_when_workflow_changes(self):
        state = BridgeState()
        first = state.update_snapshot(snapshot(), now=100)
        self.assertEqual(first.revision, 1)

        same = state.update_snapshot(snapshot(), now=101)
        self.assertEqual(same.revision, 1)

        changed = state.update_snapshot(snapshot(value=2), now=102)
        self.assertEqual(changed.revision, 2)
        self.assertEqual(changed.view["nodes"][0]["widgets"][0]["value"], 2)

    def test_viewport_metadata_does_not_change_revision(self):
        state = BridgeState()
        first_payload = snapshot()
        first_payload["workflow"]["extra"] = {"ds": {"scale": 1, "offset": [0, 0]}}
        first = state.update_snapshot(first_payload, now=100)
        second_payload = snapshot()
        second_payload["workflow"]["extra"] = {"ds": {"scale": 2, "offset": [50, 80]}}
        second = state.update_snapshot(second_payload, now=101)
        self.assertEqual(first.revision, 1)
        self.assertEqual(second.revision, 1)
        self.assertEqual(second.workflow["extra"]["ds"]["scale"], 2)

    def test_rendered_node_size_does_not_change_revision_but_position_does(self):
        state = BridgeState()
        first_payload = snapshot()
        first_payload["workflow"]["nodes"][0]["size"] = [200, 100]
        first = state.update_snapshot(first_payload, now=100)
        resized_payload = snapshot()
        resized_payload["workflow"]["nodes"][0]["size"] = [220, 120]
        resized = state.update_snapshot(resized_payload, now=101)
        self.assertEqual(first.revision, 1)
        self.assertEqual(resized.revision, 1)

        moved_payload = snapshot()
        moved_payload["workflow"]["nodes"][0]["size"] = [220, 120]
        moved_payload["workflow"]["nodes"][0]["pos"] = [50, 80]
        moved = state.update_snapshot(moved_payload, now=102)
        self.assertEqual(moved.revision, 2)

    def test_revision_conflict_reports_expected_and_actual(self):
        state = BridgeState()
        session = state.update_snapshot(snapshot(), now=100)
        with self.assertRaises(RevisionConflict) as caught:
            state.check_revision(session, 0)
        self.assertEqual(caught.exception.expected, 0)
        self.assertEqual(caught.exception.actual, 1)

    def test_boolean_is_not_a_revision(self):
        session = BridgeState().update_snapshot(snapshot(), now=100)
        with self.assertRaisesRegex(ValueError, "base_revision must be an integer"):
            BridgeState.check_revision(session, True)

    def test_undo_payload_accepts_node_ids_and_requires_history_identity(self):
        validate_undo_payload({"history_id": "cmd-1", "node_id": 7})
        validate_undo_payload({"history_id": "cmd-1", "node_id": "node-7"})
        for payload in (
            {"node_id": 7},
            {"history_id": "", "node_id": 7},
            {"history_id": "cmd-1", "node_id": True},
            {"history_id": "cmd-1", "node_id": "  "},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_undo_payload(payload)

    def test_switching_identical_blank_workflows_changes_revision_and_identity(self):
        state = BridgeState()
        first = snapshot(value=1)
        first["workflow"] = {"id": "blank-a", "nodes": [], "links": []}
        first["view"] = {"nodes": [], "links": []}
        first["workflow_id"] = "blank-a"
        session = state.update_snapshot(first, now=100)
        self.assertEqual(session.revision, 1)

        second = dict(first)
        second["workflow"] = {"id": "blank-b", "nodes": [], "links": []}
        second["workflow_id"] = "blank-b"
        switched = state.update_snapshot(second, now=101)
        self.assertEqual(switched.revision, 2)
        self.assertEqual(switched.workflow_id, "blank-b")

    def test_heartbeat_does_not_make_visible_inactive_session_recent(self):
        state = BridgeState()
        payload = snapshot()
        payload["focused"] = False
        payload["activity_at_ms"] = 1000
        session = state.update_snapshot(payload, now=100)
        state.heartbeat(
            {
                "session_id": session.session_id,
                "visible": True,
                "focused": False,
                "activity_at_ms": 1000,
            },
            now=120,
        )
        self.assertEqual(session.last_active_at, 100)

    def test_session_listing_is_compact(self):
        state = BridgeState()
        state.update_snapshot(snapshot())
        listed = state.list_sessions()
        self.assertEqual(listed[0]["node_count"], 1)
        self.assertEqual(listed[0]["link_count"], 0)
        self.assertNotIn("view", listed[0])
        self.assertNotIn("workflow", listed[0])

    def test_focused_session_wins(self):
        state = BridgeState()
        current = time.time()
        a = snapshot(session_id="a", client_id="ca")
        a["focused"] = False
        state.update_snapshot(a, now=current)
        b = snapshot(session_id="b", client_id="cb")
        state.update_snapshot(b, now=current + 1)
        self.assertEqual(state.resolve().session_id, "b")

    def test_equally_active_sessions_are_ambiguous(self):
        state = BridgeState()
        current = time.time()
        a = snapshot(session_id="a", client_id="ca")
        b = snapshot(session_id="b", client_id="cb")
        a["focused"] = False
        b["focused"] = False
        state.update_snapshot(a, now=current)
        state.update_snapshot(b, now=current)
        with self.assertRaises(SessionAmbiguous):
            state.resolve()


if __name__ == "__main__":
    unittest.main()
