"""紫晶界面的布局、旧偏好兼容与窗口区域资源契约；不访问用户界面。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from hots_chat.layout import DisplayMetrics, quick_input_layout
from hots_chat.window_settings import WindowSettings
from hots_chat.ui.native_windows import Win32Window
from tests.test_overlay_window import NativeFunction, NativeUser


class NexusPlacementTests(unittest.TestCase):
    def test_quick_input_can_follow_calibrated_chat_position(self):
        metrics = DisplayMetrics((0, 0, 1920, 1020), 1.25)
        value = quick_input_layout(metrics, height=150, position=(90, 700))
        self.assertEqual((value.x, value.y), (90, 700))
        self.assertEqual(value.width, 800)

    def test_quick_input_default_is_at_bottom_center(self):
        value = quick_input_layout(DisplayMetrics((0, 0, 1920, 1020), 1), height=120)
        self.assertEqual((value.x, value.y), (632, 844))

    def test_chat_position_clamps_inside_work_area_at_large_dpi(self):
        metrics = DisplayMetrics((10000, 10000, 11920, 11020), 2)
        value = quick_input_layout(metrics, height=330, position=(11800, 10900))
        self.assertLessEqual(value.x + value.width + 32, 11920)
        self.assertLessEqual(value.y + value.height + 80, 11020)

    def test_old_saved_coordinates_are_preserved_as_custom_placement(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            path.write_text(json.dumps({'overlay_x': 250, 'overlay_y': 410,
                                        'preferences_version': 3}), encoding='utf-8')
            settings = WindowSettings.load(path)
            self.assertEqual(settings.overlay_placement, 'custom')
            self.assertEqual((settings.overlay_x, settings.overlay_y), (250, 410))
            self.assertIsNone(settings.overlay_bottom_anchor)

    def test_new_preferences_default_to_bottom_and_anchor_roundtrips(self):
        settings = WindowSettings()
        self.assertEqual(settings.overlay_placement, 'bottom')
        settings.overlay_x, settings.overlay_y = 80, 420
        settings.record_overlay_anchor(DisplayMetrics((0, 0, 1920, 1000)), 440, 100)
        self.assertAlmostEqual(settings.overlay_bottom_anchor, .52)
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            settings.save(path)
            restored = WindowSettings.load(path)
            self.assertEqual(restored.overlay_bottom_anchor, .52)
            self.assertEqual(restored.overlay_anchor, settings.overlay_anchor)

    def test_invalid_new_placement_fields_fall_back_to_bottom_safely(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            for invalid in ({'overlay_placement': 'unknown'},
                            {'overlay_bottom_anchor': -1},
                            {'overlay_bottom_anchor': True},
                            {'overlay_bottom_anchor': float('inf')}):
                with self.subTest(value=invalid):
                    path.write_text(json.dumps(invalid), encoding='utf-8')
                    restored = WindowSettings.load(path)
                    self.assertEqual(restored.overlay_placement, 'bottom')
                    self.assertIsNone(restored.overlay_bottom_anchor)

    def test_selected_palette_roundtrips_and_unknown_palette_falls_back(self):
        settings = WindowSettings()
        settings.color_theme = 'ice'
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'preferences.json'
            settings.save(path)
            self.assertEqual(WindowSettings.load(path).color_theme, 'lavender')
            path.write_text('{"color_theme":"unknown"}', encoding='utf-8')
            self.assertEqual(WindowSettings.load(path).color_theme, 'lavender')


class RoundedWindowRegionTests(unittest.TestCase):
    def setUp(self):
        self.user = NativeUser()
        self.user.SetWindowRgn = NativeFunction(1)
        self.gdi = SimpleNamespace(CreateRoundRectRgn=NativeFunction(0x100000099),
                                   DeleteObject=NativeFunction(1))
        self.window = SimpleNamespace(update_idletasks=lambda: None, winfo_id=lambda: 22,
                                      winfo_width=lambda: 440, winfo_height=lambda: 88)

    def test_success_transfers_pointer_sized_region_ownership_to_windows(self):
        native = Win32Window(user=self.user, gdi=self.gdi)
        self.assertTrue(native.set_rounded_corners(self.window, 10))
        self.assertEqual(self.gdi.CreateRoundRectRgn.calls, [(0, 0, 441, 89, 20, 20)])
        self.assertEqual(self.user.SetWindowRgn.calls[0][1], 0x100000099)
        self.assertFalse(self.gdi.DeleteObject.calls, '成功后区域由Windows管理')

    def test_failed_region_assignment_releases_gdi_resource(self):
        self.user.SetWindowRgn.result = 0
        native = Win32Window(user=self.user, gdi=self.gdi)
        self.assertFalse(native.set_rounded_corners(self.window, 10))
        self.assertEqual(self.gdi.DeleteObject.calls, [(0x100000099,)])


if __name__ == '__main__':
    unittest.main()
