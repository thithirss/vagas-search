"""Janela do bot presa a uma área de trabalho virtual (vagas/desktop.py, 2026-11-18)."""
import os
import unittest
from unittest.mock import patch

from vagas import desktop as dk

HOME, WORK = "home-guid", "work-guid"


class FakeVDM:
    def __init__(self, where, movable=False):
        self.where, self.movable = where, movable

    def desktop_of(self, hwnd):
        return self.where.get(hwnd)

    def move(self, hwnd, desktop):
        if self.movable:
            self.where[hwnd] = desktop
        return self.movable


@unittest.skipUnless(dk.IS_WIN, "só Windows")
class GuardTests(unittest.TestCase):
    def setUp(self):
        self.shown = []
        p = patch.object(dk, "_show", side_effect=lambda h, c: self.shown.append((h, c)))
        p.start()
        self.addCleanup(p.stop)
        self.g = dk.DesktopGuard()

    def test_window_that_opened_at_work_is_hidden_until_home(self):
        vdm = FakeVDM({1: WORK})
        self.g._place(vdm, 1, HOME, WORK)
        self.assertEqual(self.shown, [(1, dk.SW_HIDE)])
        self.assertIn(1, self.g.hidden)
        self.g._place(vdm, 1, HOME, WORK)          # continua fora de casa: nada muda
        self.assertEqual(len(self.shown), 1)
        self.g._place(vdm, 1, HOME, HOME)          # voltou para casa: mostra sem roubar o foco
        self.assertEqual(self.shown[-1], (1, dk.SW_SHOWNA))
        self.assertNotIn(1, self.g.hidden)

    def test_window_at_home_is_left_alone(self):
        self.g._place(FakeVDM({1: HOME}), 1, HOME, WORK)
        self.assertEqual(self.shown, [])

    def test_uses_official_move_when_windows_allows(self):
        vdm = FakeVDM({1: WORK}, movable=True)
        self.g._place(vdm, 1, HOME, WORK)
        self.assertEqual(vdm.where[1], HOME)
        self.assertEqual(self.shown, [])

    def test_stop_never_shows_hidden_windows(self):
        self.g.hidden.add(1)
        self.g.stop()
        self.assertEqual(self.shown, [])

    def test_reads_real_desktop_and_process_tree(self):
        self.assertTrue(dk.current_desktop())
        self.assertIsInstance(dk.descendant_pids(os.getpid()), set)

    def test_guard_off_by_default(self):
        from vagas.config import Settings
        self.assertIsNone(dk.start_guard(Settings()))


if __name__ == "__main__":
    unittest.main()
