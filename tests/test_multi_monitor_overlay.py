"""合成显示器覆盖位置、DPI 与诊断导出；不读取或操作外部窗口。"""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import zipfile

from hots_chat.desktop_actions import DesktopActions, DesktopActionError, validate_action
from hots_chat.diagnostics import DiagnosticRecorder
from hots_chat.display_runtime import TkDisplayRuntime
from hots_chat.layout import DisplayMetrics
from hots_chat.ui.overlay_window import OverlayWindow
from hots_chat.ui.settings_dialog import SettingsDialog
from hots_chat.window_settings import WindowSettings, work_area_at


PRIMARY=(0,0,1920,1040)
SECONDARY=(1920,0,4480,1400)
LEFT=(-1600,-120,0,780)


class MonitorSettingsTests(unittest.TestCase):
    def test_negative_and_nearest_work_area_selection(self):
        self.assertEqual(work_area_at([PRIMARY,SECONDARY,LEFT],-800,20),LEFT)
        self.assertEqual(work_area_at([PRIMARY,SECONDARY,LEFT],2200,20),SECONDARY)
        self.assertEqual(work_area_at([PRIMARY,SECONDARY,LEFT],-2000,50),LEFT)

    def test_selected_monitor_round_trips_and_unplug_recovers(self):
        settings=WindowSettings(overlay_monitor=LEFT,overlay_x=-900,overlay_y=200)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'preferences.json'
            settings.save(path)
            loaded=WindowSettings.load(path)
        self.assertEqual(loaded.overlay_monitor,LEFT)
        loaded.recover_overlay([PRIMARY,SECONDARY])
        self.assertEqual(loaded.overlay_monitor,PRIMARY)
        self.assertGreaterEqual(loaded.overlay_x,0)

    def test_invalid_monitor_does_not_reset_other_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'preferences.json'
            path.write_text(json.dumps({'font_size':20,'overlay_monitor':[0,0,0,True]}),encoding='utf-8')
            loaded=WindowSettings.load(path)
        self.assertIsNone(loaded.overlay_monitor)
        self.assertEqual(loaded.font_size,20)

    def test_display_runtime_uses_main_window_screen_and_native_dpi(self):
        class FakeTk:
            def call(self,*args):
                if args==('tk','scaling'):return 4/3
                if args==('font','names'):return ()
        root=NS(tk=FakeTk(),winfo_x=lambda:-800,winfo_y=lambda:10)
        runtime=TkDisplayRuntime(root,dpi_reader=lambda window:144,
                                 work_areas=lambda:[PRIMARY,SECONDARY,LEFT])
        self.assertEqual(runtime.refresh(),DisplayMetrics(LEFT,1.5))
        self.assertEqual(runtime.work_areas(),[PRIMARY,SECONDARY,LEFT])


class MultiMonitorOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.areas=[PRIMARY,SECONDARY,LEFT]
        self.settings=WindowSettings(overlay_monitor=SECONDARY,overlay_x=2200,overlay_y=200,
                                     overlay_placement='custom')
        self.saved=Mock()
        self.native=NS(set_overlay_mode=Mock(),show_no_activate=Mock())
        self.overlay=OverlayWindow(self.root,self.settings,metrics=DisplayMetrics(SECONDARY,1),
            work_areas=lambda:self.areas,dpi_reader=lambda window:96 if window.winfo_x()<1920 else 144,
            native=self.native,on_position_changed=self.saved)
        self.addCleanup(self.overlay.close)
        self.root.update_idletasks()

    def test_main_monitor_selection_survives_main_window_metrics_bottom_and_restart(self):
        self.overlay.move_to_monitor(0)
        self.overlay.apply_settings(DisplayMetrics(SECONDARY,2))
        self.overlay.use_bottom_preset()
        self.assertEqual(self.overlay._metrics.work_area,PRIMARY)
        self.assertEqual(self.settings.overlay_monitor,PRIMARY)
        self.assertGreaterEqual(self.settings.overlay_x,PRIMARY[0])
        self.assertLess(self.settings.overlay_x,PRIMARY[2])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'prefs.json'
            self.settings.save(path)
            loaded=WindowSettings.load(path)
            resumed=OverlayWindow(self.root,loaded,metrics=DisplayMetrics(SECONDARY,2),
                work_areas=lambda:self.areas,native=self.native,dpi_reader=lambda window:96)
            try:
                self.assertEqual(resumed._metrics.work_area,PRIMARY)
                self.assertTrue(resumed.monitor_choices()[0]['selected'])
            finally:resumed.close()

    def test_drag_can_cross_to_negative_screen_and_uses_destination_dpi(self):
        self.overlay.set_editing(True)
        self.root.update_idletasks()
        # Windows reports the old DPI until the moved window reaches its target.
        drag_dpi=iter((144,96))
        self.overlay._dpi_reader=lambda window:next(drag_dpi)
        start=NS(x_root=self.settings.overlay_x+10,y_root=self.settings.overlay_y+10)
        self.overlay._start_drag(start)
        target=NS(x_root=-800,y_root=250)
        self.overlay._drag(target)
        self.root.update_idletasks()
        self.overlay._end_drag(target)
        self.assertEqual(self.settings.overlay_monitor,LEFT)
        self.assertEqual(self.overlay._metrics,DisplayMetrics(LEFT,1))
        self.assertLess(self.settings.overlay_x,0)
        self.assertGreaterEqual(self.settings.overlay_x,LEFT[0])
        self.assertEqual(self.settings.overlay_placement,'custom')
        self.saved.assert_called_once_with()

    def test_dpi_scaling_uses_overlay_window_instead_of_main_window(self):
        self.overlay._dpi_reader=lambda window:192
        self.overlay.move_to_monitor(1)
        self.assertEqual(self.overlay._metrics.scale,2)
        root_scale=float(self.root.tk.call('tk','scaling'))*72/96
        self.assertEqual(self.overlay._translation_font.cget('size'),
                         round(self.settings.overlay_font_size*2/root_scale))
        self.assertEqual(self.overlay._layout.width,self.settings.overlay_width*2)

    def test_injected_display_scale_is_used_when_native_dpi_reader_is_absent(self):
        self.overlay._dpi_reader=None
        self.settings.overlay_monitor=PRIMARY
        self.overlay.apply_settings(DisplayMetrics(SECONDARY,2))
        self.assertEqual(self.overlay._metrics,DisplayMetrics(PRIMARY,2))
        self.assertEqual(self.overlay._layout.width,self.settings.overlay_width*2)

    def test_unplugged_monitor_recovers_once_to_available_screen(self):
        recorder=Mock()
        self.overlay._diagnostics=recorder
        self.areas=[PRIMARY]
        self.assertTrue(self.overlay.refresh_layout())
        self.assertEqual(self.settings.overlay_monitor,PRIMARY)
        self.assertGreaterEqual(self.settings.overlay_x,0)
        self.overlay.refresh_layout()
        recoveries=[call for call in recorder.record.call_args_list if call.kwargs.get('outcome')=='recovered']
        self.assertEqual(len(recoveries),1)

    def test_monitor_changes_and_failure_evidence_survive_real_zip_export(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=DiagnosticRecorder(Path(directory)/'logs',enabled=True)
            self.addCleanup(recorder.close)
            self.overlay._diagnostics=recorder
            self.overlay.move_to_monitor(0)
            self.overlay._persist_position()
            self.overlay._editing=True
            self.overlay._start_drag(NS(x_root=self.settings.overlay_x+10,y_root=self.settings.overlay_y+10))
            self.overlay._drag(NS(x_root=-800,y_root=200))
            self.overlay._end_drag(NS(x_root=-800,y_root=200))
            with self.assertRaises(ValueError):self.overlay.move_to_monitor(99)
            self.overlay.move_to_monitor(1)
            self.areas=[PRIMARY]
            self.overlay.refresh_layout()
            self.overlay._on_position_changed=Mock(side_effect=OSError('private-storage-path'))
            self.overlay._persist_position()
            with patch.object(self.overlay.window,'geometry',side_effect=tk.TclError('private-window-name')):
                with self.assertRaises(tk.TclError):self.overlay.move_to_monitor(0)
            target=recorder.export_report(Path(directory)/'monitors.zip')
            recorder.close()
            with zipfile.ZipFile(target) as archive:
                rows=[json.loads(line) for name in archive.namelist() if '.log' in name
                      for line in archive.read(name).decode('utf-8').splitlines() if line]
            placements=[row for row in rows if row.get('operation')=='monitor']
            outcomes={row.get('outcome') for row in placements}
            self.assertTrue({'started','applied','saved','rejected','recovered','error'}<=outcomes)
            applied=next(row for row in placements if row.get('outcome')=='applied')
            linked=[row['outcome'] for row in placements if row.get('request_ref')==applied['request_ref']]
            self.assertEqual(linked,['started','applied','saved'])
            reasons={row.get('reason') for row in placements}
            self.assertTrue({'monitor_unavailable','save_failed','placement_failed'}<=reasons)
            drag=next(row for row in placements if row.get('outcome')=='applied' and row.get('reason')=='drag')
            self.assertEqual((drag['origin_x'],drag['origin_y']),(LEFT[0],LEFT[1]))
            exported=json.dumps(placements)
            self.assertNotIn('private-',exported)
            self.assertNotIn('title',exported)

    def test_diagnostic_failure_cannot_prevent_monitor_movement(self):
        self.overlay._diagnostics=NS(record=Mock(side_effect=RuntimeError('logger failed')))
        self.overlay.move_to_monitor(0)
        self.assertEqual(self.settings.overlay_monitor,PRIMARY)

    def test_native_select_then_bottom_applies_selected_screen_in_same_save(self):
        dialog=SettingsDialog.__new__(SettingsDialog)
        dialog.window=NS(overlay=self.overlay,quick_input=NS(refresh_layout=Mock()),_layout_metrics=DisplayMetrics(SECONDARY,1))
        dialog.values={'overlay_enabled':Mock(),'overlay_style':Mock()}
        dialog._monitor_choices=self.overlay.monitor_choices()
        dialog.monitor_choice=NS(current=lambda:0)
        def save(before_apply=None):
            before_apply()
            self.overlay.apply_settings(DisplayMetrics(SECONDARY,1))
            return True
        dialog.save=Mock(side_effect=save)
        dialog.use_bottom_preset()
        self.assertEqual(self.settings.overlay_monitor,PRIMARY)
        self.assertEqual(self.settings.overlay_placement,'bottom')
        self.assertLess(self.settings.overlay_x,PRIMARY[2])
        dialog.save.assert_called_once()

    def test_native_edit_on_same_monitor_keeps_custom_placement(self):
        dialog=SettingsDialog.__new__(SettingsDialog)
        dialog.window=NS(overlay=self.overlay)
        dialog.values={'overlay_enabled':Mock()}
        dialog._monitor_choices=self.overlay.monitor_choices()
        dialog.monitor_choice=NS(current=lambda:1)
        dialog.save=lambda before_apply:(before_apply() or True)
        with patch.object(self.overlay,'move_to_monitor') as move:
            dialog.edit_overlay()
            move.assert_not_called()
        self.assertEqual(self.settings.overlay_placement,'custom')


class MonitorActionTests(unittest.TestCase):
    def test_monitor_command_validation_rejects_non_integer_or_unbounded_ids(self):
        for value in (True,'1',-1,32,None):
            with self.subTest(value=value),self.assertRaises(DesktopActionError):
                validate_action('overlay_move',{'monitor':value})
        self.assertEqual(validate_action('overlay_move',{'monitor':0}),{'monitor':0})

    def test_save_failure_restores_monitor_and_emits_failure_and_rollback(self):
        settings=WindowSettings(overlay_monitor=SECONDARY)
        original=deepcopy(asdict(settings))
        overlay=NS(_trace_monitor=Mock(),move_to_monitor=lambda index:setattr(settings,'overlay_monitor',PRIMARY))
        window=NS(settings=settings,closed=False,overlay=overlay,capture_settings_state=Mock(return_value=()),
                  restore_settings_state=Mock(),apply_settings=Mock(),save_settings=Mock(side_effect=OSError('private-path')))
        actions=DesktopActions(window)
        self.addCleanup(actions.close)
        with self.assertRaises(DesktopActionError):actions.dispatch('overlay_move',{'monitor':0})
        self.assertEqual(asdict(settings),original)
        self.assertEqual(overlay._trace_monitor.call_args_list[0].kwargs,{'reason':'save_failed'})
        self.assertEqual(overlay._trace_monitor.call_args_list[1].kwargs,{'reason':'settings_rollback'})


if __name__=='__main__':unittest.main()
