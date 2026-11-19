"""Janela do bot presa a UMA área de trabalho virtual do Windows (pedido do usuário, 2026-11-18).

"se eu trocar de área de trabalho ele vai junto… quero que ele mantenha em apenas uma para eu usar no trabalho".
No Windows, janela NOVA abre na área de trabalho em que a pessoa está, e o bot abre o Chrome de novo a cada leva,
tarefa e pop-up. `DesktopGuard` (uma thread no processo que abriu o Chrome) cuida disso:

- área "de casa" = a escolhida no painel (Preferências → Extras → "Prender nesta área de trabalho") ou, sem
  escolha, a área em que o bot estava na primeira vez; guardada em `data/desktop_home.json`;
- janela do Chrome do bot que aparece em OUTRA área: tenta `MoveWindowToDesktop` (API oficial do Windows); se o
  Windows recusar (a API só move janela do próprio processo), ESCONDE a janela (SW_HIDE) enquanto você estiver fora
  de casa e mostra de novo (sem roubar o foco) quando você volta — ela reaparece na área de casa;
- o Chrome continua trabalhando escondido (testado: clique, digitação, navegação, timers e rAF). Aba nova faz o
  Chrome mostrar a janela de novo: por isso a checagem é a cada 0,3 s (pode piscar o botão na barra de tarefas).

Só Windows; em outro sistema tudo vira no-op. Só mexe nas janelas de Chrome que são FILHAS deste processo
(árvore de processos), nunca no Chrome pessoal.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from .config import DATA_DIR

HOME_FILE = DATA_DIR / "desktop_home.json"
BEAT_FILE = DATA_DIR / "logs" / "desktop_guard.json"   # sinal de vida do vigia (para conferir de fora)
IS_WIN = os.name == "nt"

if IS_WIN:
    import ctypes
    import ctypes.wintypes as wt
    import winreg

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def _guid(s: str) -> GUID:
        g = GUID()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(s).bytes_le, 16)
        return g

    def _str(g: GUID) -> str:
        return str(uuid.UUID(bytes_le=bytes(bytearray(ctypes.string_at(ctypes.byref(g), 16)))))

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    CLSID_VDM = "aa509086-5ca9-4c25-8f95-589d3c07b48a"
    IID_VDM = "a5cd92ff-29be-454c-8d04-d82879fb3f1b"
    NULL_GUID = "00000000-0000-0000-0000-000000000000"
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, GW_OWNER = -20, 0x80, 4
    SW_HIDE, SW_SHOWNA = 0, 8
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]


def _show(hwnd, cmd) -> None:
    """ShowWindowAsync: não espera a janela de outro processo responder. ShowWindow comum é síncrono entre threads e
    travou o vigia dentro do bot em 18/11 (a janela ficou visível na área do trabalho)."""
    user32.ShowWindowAsync(hwnd, cmd)


class VDM:
    """IVirtualDesktopManager (shobjidl.h) por ctypes: só os 3 métodos documentados."""

    def __init__(self):
        ole32.CoInitializeEx(None, 2)   # COINIT_APARTMENTTHREADED, nesta thread
        self.p = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(_guid(CLSID_VDM)), None, 0x17, ctypes.byref(_guid(IID_VDM)), ctypes.byref(self.p))
        if hr != 0 or not self.p:
            raise OSError(f"VirtualDesktopManager indisponível (hr={hr & 0xFFFFFFFF:#x})")
        vtbl = ctypes.cast(self.p, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        P = ctypes.WINFUNCTYPE
        self._on_current = P(ctypes.c_long, ctypes.c_void_p, wt.HWND, ctypes.POINTER(wt.BOOL))(vtbl[3])
        self._desktop_id = P(ctypes.c_long, ctypes.c_void_p, wt.HWND, ctypes.POINTER(GUID))(vtbl[4])
        self._move = P(ctypes.c_long, ctypes.c_void_p, wt.HWND, ctypes.POINTER(GUID))(vtbl[5])

    def on_current(self, hwnd) -> bool | None:
        b = wt.BOOL()
        return bool(b.value) if self._on_current(self.p, hwnd, ctypes.byref(b)) == 0 else None

    def desktop_of(self, hwnd) -> str | None:
        g = GUID()
        if self._desktop_id(self.p, hwnd, ctypes.byref(g)) != 0:
            return None
        s = _str(g)
        return None if s == NULL_GUID else s

    def move(self, hwnd, desktop: str) -> bool:
        return self._move(self.p, hwnd, ctypes.byref(_guid(desktop))) == 0


def current_desktop() -> str | None:
    """Área de trabalho em que a pessoa está agora (o Explorer grava no registro a cada troca)."""
    if not IS_WIN:
        return None
    for path in (r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops",):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                raw, _ = winreg.QueryValueEx(k, "CurrentVirtualDesktop")
                if raw and len(raw) == 16:
                    return str(uuid.UUID(bytes_le=bytes(raw)))
        except OSError:
            pass
    # Windows 10: por sessão
    try:
        sid = wt.DWORD()
        kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(sid))
        path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\SessionInfo\{sid.value}\VirtualDesktops"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            raw, _ = winreg.QueryValueEx(k, "CurrentVirtualDesktop")
            if raw and len(raw) == 16:
                return str(uuid.UUID(bytes_le=bytes(raw)))
    except OSError:
        pass
    return None


def desktop_count() -> int:
    if not IS_WIN:
        return 0
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops") as k:
            raw, _ = winreg.QueryValueEx(k, "VirtualDesktopIDs")
            return len(raw or b"") // 16 or 1
    except OSError:
        return 1


def load_home() -> str | None:
    try:
        return json.loads(HOME_FILE.read_text(encoding="utf-8")).get("desktop") or None
    except Exception:
        return None


def save_home(desktop: str | None = None) -> str | None:
    d = desktop or current_desktop()
    if d:
        HOME_FILE.parent.mkdir(parents=True, exist_ok=True)
        HOME_FILE.write_text(json.dumps({"desktop": d, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
    return d


def descendant_pids(root: int) -> set[int]:
    """PIDs de todos os processos descendentes de `root` (o Chrome do bot nasce do driver, que nasce daqui)."""
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)
    if snap in (0, -1, ctypes.c_void_p(-1).value):
        return set()
    children: dict[int, list[int]] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            children.setdefault(e.th32ParentProcessID, []).append(e.th32ProcessID)
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    out, stack = set(), [root]
    while stack:
        for c in children.get(stack.pop(), []):
            if c not in out and c != root:
                out.add(c)
                stack.append(c)
    return out


def chrome_windows(pids: set[int], include_hidden: set[int] | None = None) -> list[int]:
    """Janelas principais (Chrome_WidgetWin_1, sem dono, não-ferramenta) dos processos em `pids`."""
    found: list[int] = []
    cls = ctypes.create_unicode_buffer(64)

    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value != "Chrome_WidgetWin_1" or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if user32.IsWindowVisible(hwnd) or (include_hidden and hwnd in include_hidden):
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


class DesktopGuard(threading.Thread):
    def __init__(self, interval: float = 0.3, root_pid: int | None = None):
        super().__init__(name="desktop-guard", daemon=True)
        self.interval = interval
        self.root = root_pid or os.getpid()
        self.hidden: set[int] = set()
        self._halt = threading.Event()
        self.tick = time.time()
        self.can_move: bool | None = None
        self.log: list[str] = []

    def stop(self) -> None:
        # NÃO mostra as janelas escondidas: você pode estar na área do trabalho; o Chrome fecha junto com o bot
        self._halt.set()

    def run(self) -> None:
        try:
            vdm = VDM()
        except Exception as e:
            self.log.append(f"vigia da área de trabalho desligado: {e}")
            return
        home = load_home() or save_home()
        pids: set[int] = set()
        last_pids = 0.0
        last_beat = 0.0
        while not self._halt.wait(self.interval):
            self.tick = time.time()
            if time.time() - last_beat > 5:
                last_beat = time.time()
                try:
                    BEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
                    BEAT_FILE.write_text(json.dumps({"pid": os.getpid(), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                     "hidden": len(self.hidden), "windows": len(pids), "errors": self.log[-3:]}),
                                         encoding="utf-8")
                except Exception:
                    pass
            try:
                home = load_home() or home     # o painel pode trocar a área de casa a qualquer momento
                cur = current_desktop()
                if not home or not cur:
                    continue
                if time.time() - last_pids > 3:
                    pids, last_pids = descendant_pids(self.root), time.time()
                self.hidden = {h for h in self.hidden if user32.IsWindow(h)}
                for hwnd in chrome_windows(pids, self.hidden):
                    self._place(vdm, hwnd, home, cur)
            except Exception as e:   # o vigia nunca derruba o bot
                self.log.append(str(e)[:200])

    def _place(self, vdm: VDM, hwnd: int, home: str, cur: str) -> None:
        if hwnd in self.hidden:
            if cur == home:                           # voltou para casa: mostra (cai na área atual = casa)
                _show(hwnd, SW_SHOWNA)
                self.hidden.discard(hwnd)
            return
        where = vdm.desktop_of(hwnd)
        if where in (None, home):
            return
        if self.can_move is not False and vdm.move(hwnd, home):
            self.can_move = True
            return
        self.can_move = False
        if cur != home:
            _show(hwnd, SW_HIDE)
            self.hidden.add(hwnd)
        else:
            # está numa área que não é a de casa e você está em casa: esconde e mostra para ela vir para cá
            _show(hwnd, SW_HIDE)
            time.sleep(0.15)
            _show(hwnd, SW_SHOWNA)


_guard: DesktopGuard | None = None


def start_guard(settings) -> DesktopGuard | None:
    """Liga o vigia uma vez por processo (Browser.start chama). Desligado em `browser.pin_desktop: false`."""
    global _guard
    if not IS_WIN or not getattr(settings.browser, "pin_desktop", False):
        return None
    if _guard is not None and _guard.is_alive() and time.time() - _guard.tick > 15:
        _guard._halt.set()            # travado (não volta da última checagem): sobe outro
        _guard = None
    if _guard is None or not _guard.is_alive():
        _guard = DesktopGuard()
        _guard.start()
    return _guard


def stop_guard() -> None:
    global _guard
    if _guard is not None:
        _guard.stop()
        _guard = None
