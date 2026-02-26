import unittest

from support import ROOT, SCRIPTS_DIR  # noqa: F401


class TestWsEventTapTiming(unittest.TestCase):
    def test_active_start_then_success_sets_durations(self):
        # Import via support.py sys.path injection (see other tests)
        from ws_event_tap import apply_ws_event_to_metrics, _derive_durations  # type: ignore

        metrics = {}
        # executing with node id should set active_started_ts
        patch1 = apply_ws_event_to_metrics(
            metrics,
            msg_type="executing",
            data={"prompt_id": "pid123", "node": "42"},
            recv_ts=100.0,
        )
        metrics.update(patch1)
        self.assertEqual(metrics.get("prompt_id"), "pid123")
        self.assertEqual(metrics.get("active_started_ts"), 100.0)

        # execution_success should set exec_ended_ts
        patch2 = apply_ws_event_to_metrics(
            metrics,
            msg_type="execution_success",
            data={"prompt_id": "pid123", "timestamp": 101.5},
            recv_ts=200.0,
        )
        metrics.update(patch2)
        # timestamp must look like epoch seconds; 101.5 should be ignored in favor of recv_ts
        self.assertEqual(metrics.get("exec_ended_ts"), 200.0)

        deriv = _derive_durations(metrics)
        metrics.update(deriv)
        self.assertAlmostEqual(metrics.get("active_runtime_sec"), 100.0, places=6)

    def test_execution_start_keeps_earliest(self):
        from ws_event_tap import apply_ws_event_to_metrics  # type: ignore

        metrics = {"exec_started_ts": 50.0}
        patch = apply_ws_event_to_metrics(
            metrics,
            msg_type="execution_start",
            data={"prompt_id": "pidA"},
            recv_ts=60.0,
        )
        # Should keep earliest (50.0), not overwrite with 60.0
        self.assertEqual(patch.get("exec_started_ts"), 50.0)


if __name__ == "__main__":
    unittest.main()

