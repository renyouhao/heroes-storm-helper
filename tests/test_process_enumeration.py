import ctypes
import unittest
from unittest.mock import patch
from hots_chat.acquisition.memory import find_game_processes


class Function:
    def __call__(self,*args):return ctypes.c_void_p(-1).value


class Kernel:
    def __init__(self):
        self.CreateToolhelp32Snapshot=Function()
        self.Process32FirstW=Function()
        self.Process32NextW=Function()
        self.CloseHandle=Function()


class ProcessEnumerationTests(unittest.TestCase):
    def test_repeated_enumeration_does_not_retain_new_ctypes_classes(self):
        with patch('hots_chat.acquisition.memory.ctypes.WinDLL',side_effect=lambda *a,**k:Kernel()):
            find_game_processes()
            before=len(ctypes._pointer_type_cache)
            for _ in range(30):self.assertEqual(find_game_processes(),[])
            self.assertEqual(len(ctypes._pointer_type_cache),before)
