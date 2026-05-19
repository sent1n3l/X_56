import tempfile
import unittest
from pathlib import Path

from x56_configurator import X56Engine


class X56EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.state = self.tmp_path / "state.json"
        self.templates = self.tmp_path / "templates.json"
        self.templates.write_text(
            '{"latest":"1.0","1.0":{"joystick_bindings":{"pitch":"joy_y"},"throttle_bindings":{"throttle_forward":"axis_z"}}}',
            encoding="utf-8",
        )
        self.engine = X56Engine(state_file=self.state, version_file=self.templates)

    def tearDown(self):
        self.tmp.cleanup()

    def test_template_for_unknown_version_uses_latest(self):
        template = self.engine.template_for_version("unknown")
        self.assertIn("joystick_bindings", template)
        self.assertIn("throttle_bindings", template)

    def test_apply_fix_rewrites_devices_and_writes_state(self):
        cfg = self.tmp_path / "actionmaps.xml"
        cfg.write_text("<ActionMaps><device name='Old Stick'/></ActionMaps>", encoding="utf-8")

        messages = self.engine.apply_fix(cfg, "1.0", "X56 H.O.T.A.S. Joystick", "X56 H.O.T.A.S. Throttle")

        text = cfg.read_text(encoding="utf-8")
        self.assertIn("X56 H.O.T.A.S. Joystick", text)
        self.assertIn("X56 H.O.T.A.S. Throttle", text)
        self.assertNotIn("Old Stick", text)
        self.assertTrue((self.tmp_path / "actionmaps.xml.bak").exists())
        self.assertTrue(any("integrity snapshot" in m.lower() for m in messages))

    def test_cross_check_detects_drift(self):
        cfg = self.tmp_path / "actionmaps.xml"
        cfg.write_text("<ActionMaps/>", encoding="utf-8")
        self.engine.apply_fix(cfg, "1.0", "X56 H.O.T.A.S. Joystick", "X56 H.O.T.A.S. Throttle")

        self.assertFalse(self.engine.cross_check(cfg))
        cfg.write_text(cfg.read_text(encoding="utf-8") + "\n<!-- changed -->", encoding="utf-8")
        self.assertTrue(self.engine.cross_check(cfg))


if __name__ == "__main__":
    unittest.main()
