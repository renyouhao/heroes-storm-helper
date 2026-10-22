import tempfile
import unittest
import ctypes
from unittest.mock import patch
from pathlib import Path
from hots_chat.window_settings import WindowSettings,screen_bounds
from hots_chat.hotkeys import HotkeyManager

class SettingsTests(unittest.TestCase):
    def test_overlay_recovery_uses_scaled_width_without_overwriting_logical_preference(self):
        s=WindowSettings(overlay_x=1800,overlay_y=1000,overlay_width=440)
        s.recover_overlay([(0,0,1920,1000)],scale=2,height=600)
        self.assertEqual((s.overlay_x,s.overlay_y),(1040,400))
        self.assertEqual(s.overlay_width,440)

    def test_overlay_and_target_round_trip_without_chat_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            s=WindowSettings(overlay_x=-900,overlay_y=200,overlay_alpha=.75,target_language='en',auto_translate=False)
            s.save(p);restored=WindowSettings.load(p)
            self.assertEqual((restored.overlay_x,restored.overlay_y),(-900,200))
            self.assertEqual(restored.target_language,'en')
            self.assertFalse(restored.auto_translate)
            self.assertEqual(restored.overlay_alpha,.75)

    def test_old_default_settings_gain_quick_input_shortcut_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json';p.write_text('{"hotkeys":{}}',encoding='utf-8')
            s=WindowSettings.load(p)
            self.assertEqual(s.hotkeys.get('focus'),'Ctrl+T')
            s.hotkeys={};s.save(p)
            self.assertEqual(WindowSettings.load(p).hotkeys,{})

    def test_overlay_recovers_on_removed_monitor_and_clamps_dimensions(self):
        s=WindowSettings(overlay_x=-900,overlay_y=100,overlay_width=9999,overlay_font_size=2,overlay_alpha=.1)
        s.recover_overlay([(0,0,1920,1080)])
        self.assertEqual(s.overlay_width,900)
        self.assertEqual(s.overlay_font_size,10)
        self.assertEqual(s.overlay_alpha,.35)
        self.assertGreaterEqual(s.overlay_x,0)
        self.assertLessEqual(s.overlay_x+s.overlay_width,1920)

    def test_invalid_overlay_or_target_preferences_fall_back_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            for data in ('{"overlay_enabled":"yes"}','{"target_language":"bad"}','{"overlay_timeout":1e999}','{"overlay_x":[]}'):
                p.write_text(data,encoding='utf-8')
                self.assertEqual(WindowSettings.load(p).target_language,'ko')
                self.assertTrue(WindowSettings.load(p).overlay_enabled)

    def test_monitor_polling_does_not_retain_new_ctypes_types(self):
        class Function:
            def __init__(self,result):self.result=result
            def __call__(self,*args):return self.result
        class User:
            GetMonitorInfoW=Function(False)
            EnumDisplayMonitors=Function(False)
            GetSystemMetrics=Function(1080)
        with patch('ctypes.WinDLL',return_value=User()):
            self.assertEqual(screen_bounds(),[(0,0,1080,1080)])
            before=len(ctypes._pointer_type_cache)
            for _ in range(30):self.assertEqual(screen_bounds(),[(0,0,1080,1080)])
            self.assertEqual(len(ctypes._pointer_type_cache),before)

    def test_alpha_font_and_coordinates_clamp(self):
        s=WindowSettings(font_size=99,alpha=.01,x=99999,y=-99999)
        s.clamp(1920,1080,700,500)
        self.assertEqual(s.font_size,32); self.assertEqual(s.alpha,.35)
        self.assertLessEqual(s.x,1220);self.assertGreaterEqual(s.y,0)
    def test_settings_persist_and_invalid_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            s=WindowSettings(mode='compact',locked=True);s.save(p)
            self.assertEqual(WindowSettings.load(p).mode,'compact')
            p.write_text('{',encoding='utf-8')
            self.assertEqual(WindowSettings.load(p).mode,'normal')
    def test_monitor_removed_recovers_but_connected_negative_monitor_kept(self):
        s=WindowSettings(x=-1000,y=100)
        s.recover([(-1920,0,0,1080),(0,0,1920,1080)],700,500)
        self.assertEqual(s.x,-1000)
        s.recover([(0,0,1920,1080)],700,500)
        self.assertGreaterEqual(s.x,0)
    def test_hotkey_disabled_by_default(self):
        h=HotkeyManager();h.start({},lambda x:None)
        self.assertIsNone(h.thread)
    def test_invalid_hotkey_preferences_recover_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            for data in ('{"hotkeys":{"translate":"T"}}','{"hotkeys":{"copy":3}}','{"hotkeys":{"send":"Ctrl+T"}}'):
                p.write_text(data,encoding='utf-8')
                self.assertEqual(WindowSettings.load(p).hotkeys,{})
    def test_nonfinite_or_wrong_numeric_preference_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            for data in ('{"font_size":1e999}','{"x":[]}','{"alpha":"bad"}'):
                p.write_text(data,encoding='utf-8')
                self.assertEqual(WindowSettings.load(p).font_size,12)
