"""相对位置与旧偏好迁移：只使用数学输入和临时配置文件。"""
import json
from pathlib import Path
import tempfile
import unittest

from hots_chat import layout
from hots_chat.layout import DisplayMetrics, main_layout, overlay_layout
from hots_chat.window_settings import WindowSettings


class RelativePlacementTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(layout, 'relative_position', None)),
                        '需要按可用剩余空间记录窗口相对位置。')
        self.assertTrue(callable(getattr(layout, 'anchored_position', None)))

    def test_main_position_uses_remaining_client_space_and_survives_dpi_change(self):
        one = DisplayMetrics((0, 0, 1920, 1040), 1)
        anchor = layout.relative_position(one, x=412, y=140, width=1080,
                                          height=720, decorated=True)
        self.assertEqual(anchor, (.5, .5))
        two = main_layout(DisplayMetrics((0, 0, 2560, 1400), 1.25), anchor=anchor)
        self.assertEqual((two.x, two.y, two.width, two.height), (595, 225, 1350, 900))
        four = main_layout(DisplayMetrics((0, 0, 3840, 2080), 2), anchor=anchor)
        self.assertEqual((four.x, four.y, four.width, four.height), (824, 280, 2160, 1440))
        returned = main_layout(one, anchor=anchor)
        self.assertEqual((returned.x, returned.y), (412, 140))

    def test_overlay_anchor_keeps_relative_placement_without_double_scaling(self):
        one = DisplayMetrics((0, 0, 1920, 1040), 1)
        anchor = layout.relative_position(one, x=740, y=430, width=440, height=180)
        self.assertEqual(anchor, (.5, .5))
        four = overlay_layout(DisplayMetrics((0, 0, 3840, 2080), 2),
                              width=440, height=360, anchor=anchor)
        self.assertEqual((four.x, four.y, four.width), (1480, 860, 880))

    def test_temporary_small_work_area_does_not_destroy_anchor(self):
        anchor = (.9, .9)
        small = overlay_layout(DisplayMetrics((0, 0, 800, 600), 1),
                               width=440, height=180, anchor=anchor)
        self.assertEqual((small.x, small.y), (324, 378))
        returned = overlay_layout(DisplayMetrics((0, 0, 1920, 1040), 1),
                                  width=440, height=180, anchor=anchor)
        self.assertEqual((returned.x, returned.y), (1332, 774))

    def test_position_without_free_space_is_bounded_and_has_no_division_by_zero(self):
        metrics = DisplayMetrics((-1000, -100, -200, 500), 1)
        anchor = layout.relative_position(metrics, x=0, y=0, width=800, height=600)
        self.assertEqual(anchor, (0., 0.))
        self.assertEqual(layout.anchored_position(metrics, width=800, height=600,
                                                  anchor=(1, 1)), (-1000, -100))

    def test_old_absolute_layout_remains_available_without_an_anchor(self):
        metrics = DisplayMetrics((0, 0, 1920, 1040), 1)
        main = main_layout(metrics, x=120, y=80)
        overlay = overlay_layout(metrics, width=440, x=40, y=180, height=180)
        self.assertEqual((main.x, main.y), (120, 80))
        self.assertEqual((overlay.x, overlay.y), (40, 180))


class PlacementPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(WindowSettings, 'migrate_display', None)),
                        '需要将旧96-DPI虚拟坐标迁移到原生坐标一次。')

    def test_legacy_coordinates_migrate_once_at_actual_125_percent_baseline(self):
        settings = WindowSettings(x=120, y=80, overlay_x=40, overlay_y=180,
                                  target_language='en', alpha=.75)
        metrics = DisplayMetrics((0, 0, 1920, 1030), 1.25)
        self.assertTrue(settings.migrate_display(metrics))
        self.assertEqual((settings.x, settings.y, settings.overlay_x, settings.overlay_y),
                         (150, 100, 50, 225))
        self.assertEqual(settings.preferences_version, 3)
        self.assertFalse(settings.migrate_display(metrics))
        self.assertEqual((settings.x, settings.y, settings.overlay_x, settings.overlay_y),
                         (150, 100, 50, 225))
        self.assertEqual((settings.target_language, settings.alpha), ('en', .75))
        self.assertEqual(settings.hotkeys, {'focus': 'Ctrl+T'})

    def test_records_real_main_and_overlay_positions_as_anchors(self):
        settings = WindowSettings(x=412, y=140, overlay_x=740, overlay_y=430)
        metrics = DisplayMetrics((0, 0, 1920, 1040), 1)
        settings.record_main_anchor(metrics, 1080, 720)
        settings.record_overlay_anchor(metrics, 440, 180)
        self.assertEqual(settings.main_anchor, (.5, .5))
        self.assertEqual(settings.overlay_anchor, (.5, .5))

    def test_native_coordinates_and_anchors_roundtrip_without_remigration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            settings = WindowSettings(x=150, y=100, overlay_x=50, overlay_y=225,
                                      preferences_version=3, main_anchor=(.5, .5),
                                      overlay_anchor=(.2, .3))
            settings.save(path)
            restored = WindowSettings.load(path)
            self.assertEqual(restored.main_anchor, (.5, .5))
            self.assertEqual(restored.overlay_anchor, (.2, .3))
            self.assertFalse(restored.migrate_display(DisplayMetrics((0, 0, 3840, 2080), 2)))
            self.assertEqual((restored.x, restored.overlay_x), (150, 50))

    def test_invalid_or_nonfinite_anchor_cannot_break_window_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            for anchor in ([float('nan'), .5], [2, .5], [.5], 'invalid', [True, .5]):
                with self.subTest(anchor=anchor):
                    path.write_text(json.dumps({'preferences_version': 3,
                                               'main_anchor': anchor}), encoding='utf-8')
                    self.assertIsNone(WindowSettings.load(path).main_anchor)


if __name__ == '__main__':
    unittest.main()
