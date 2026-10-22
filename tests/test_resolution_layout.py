"""单屏分辨率/DPI数学验证，不读取用户窗口。"""
import unittest
from types import SimpleNamespace

try:
    from hots_chat.layout import (
        DisplayMetrics, bound_rect, display_metrics, main_layout,
        overlay_layout, quick_input_layout,
    )
except ModuleNotFoundError:
    DisplayMetrics = None


class ResolutionLayoutTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(DisplayMetrics, '需要统一工作区与Tk缩放布局接口')

    def assert_inside(self, layout, work):
        left, top, right, bottom = work
        self.assertGreaterEqual(layout.x, left)
        self.assertGreaterEqual(layout.y, top)
        self.assertLessEqual(layout.x + layout.width, right)
        self.assertLessEqual(layout.y + layout.height, bottom)
        self.assertGreater(layout.width, 0)
        self.assertGreater(layout.height, 0)

    def test_three_resolutions_four_scales_keep_all_windows_inside_work_area(self):
        for width, height in ((1920,1080),(2560,1440),(3840,2160)):
            for scale in (1,1.25,1.5,2):
                with self.subTest(resolution=(width,height), scale=scale):
                    work=(0,0,width,height-round(40*scale))
                    metrics=DisplayMetrics(work,scale)
                    for mode in ('normal','compact'):
                        value=main_layout(metrics,mode=mode,x=99999,y=99999,font_size=32)
                        self.assert_inside(value,work)
                        self.assertLessEqual(value.min_width,value.width)
                        self.assertLessEqual(value.min_height,value.height)
                    self.assert_inside(overlay_layout(metrics,width=900,x=99999,y=99999,height=99999),work)
                    self.assert_inside(quick_input_layout(metrics,font_size=32,height=300*scale),work)

    def test_scale_only_changes_geometry_once_and_font_points_remain_an_input(self):
        one=DisplayMetrics((0,0,3840,2120),1)
        two=DisplayMetrics((0,0,3840,2080),2)
        self.assertEqual(overlay_layout(one,width=440).width,440)
        self.assertEqual(overlay_layout(two,width=440).width,880)
        self.assertEqual(main_layout(one,font_size=12).row_height,40)
        self.assertEqual(main_layout(two,font_size=12).row_height,80)
        self.assertTrue(main_layout(DisplayMetrics((0,0,1920,1000),2)).collapse_details)

    def test_current_tk_scaling_is_the_only_geometry_scale_source(self):
        calls=[]
        def call(*args):
            calls.append(args)
            return 8/3  # 192 dpi: Tk pixels per point, equivalent to 200%.
        root=SimpleNamespace(tk=SimpleNamespace(call=call))
        metrics=display_metrics(root,[(0,0,3840,2080)])
        self.assertEqual(metrics,DisplayMetrics((0,0,3840,2080),2))
        self.assertEqual(calls,[('tk','scaling')])

    def test_arbitrary_visible_work_area_and_oversized_rect_are_bounded(self):
        metrics=DisplayMetrics((100,60,1300,860),1.5)
        value=bound_rect(metrics,width=3000,height=2000,x=-400,y=5000)
        self.assertEqual((value.x,value.y,value.width,value.height),(100,60,1200,800))

