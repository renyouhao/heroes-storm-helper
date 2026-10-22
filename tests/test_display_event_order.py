"""系统先调整窗口、显示轮询后到时，不得覆盖用户保存的相对位置。"""
import unittest

from tests import test_adaptive_display_flow as flow


class DisplayEventOrderTests(unittest.TestCase):
    def test_configure_before_poll_preserves_anchor_and_applies_new_display(self):
        fixture = flow.AdaptiveDisplayFlowTests()
        for locked in (False, True):
            with self.subTest(locked=locked), fixture.app(width=3840, height=2160, scale=2) as app:
                root, window, settings, screen, _ = app
                settings.main_anchor = (.8, .8)
                settings.locked = locked
                window.apply_settings(layout_only=True)
                root.update()
                original = (settings.x, settings.y)
                screen.set(1920, 1080, 1.25)
                # 模拟WM先投递缩屏后的Configure；故意不先触发低频事件泵。
                root.geometry('1350x900+10550+10030')
                root.update()
                self.assertEqual(settings.main_anchor, (.8, .8))
                self.assertEqual(window._layout_metrics.scale, 1.25)
                self.assertEqual((root.winfo_width(), root.winfo_height()), (1350, 900))
                fixture.refresh_screen(root, window, screen, 3840, 2160, 2)
                self.assertEqual(settings.main_anchor, (.8, .8))
                self.assertEqual((settings.x, settings.y), original)


if __name__ == '__main__':
    unittest.main()
