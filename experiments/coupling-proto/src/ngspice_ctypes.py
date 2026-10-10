"""Shared libngspice ctypes wrapper for Bench prototype.
Returns handles with instrumentation: senddata times, vsrc calls, stat log.
"""
import ctypes, time

LIB = "/usr/lib/libngspice.so"

class VectorInfo(ctypes.Structure):
    _fields_ = [("v_name", ctypes.c_char_p),
                ("v_type", ctypes.c_int),
                ("v_flags", ctypes.c_short),
                ("v_realdata", ctypes.POINTER(ctypes.c_double)),
                ("v_compdata", ctypes.c_void_p),
                ("v_length", ctypes.c_int)]

SendChar = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
SendStat = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
ControlledExit = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
SendData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
SendInitData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
BGThreadRunning = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)
GetVSRCData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_double), ctypes.c_double, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
GetISRCData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_double), ctypes.c_double, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
GetSyncData = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_double, ctypes.POINTER(ctypes.c_double), ctypes.c_double, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)

class NgSpice:
    def __init__(self, libpath=LIB, ident=0):
        self.lib = ctypes.CDLL(libpath)
        self.ident = ident
        self.log_lines = []
        self.stat_lines = []
        self.senddata_times = []   # reserved (unused: SendData not decoded; timing done externally)
        self.senddata_n = 0      # accepted-step proxy: incremented per SendData callback
        self.vsrc_calls = 0
        self.vsrc_times = []
        self.sync_calls = 0
        self.t_avr = 0.0
        self.t_spice = 0.0
        self.t_glue = 0.0
        self._cbs = []
        self._setup_protos()

    def _setup_protos(self):
        L = self.lib
        L.ngSpice_Init.argtypes = [SendChar, SendStat, ControlledExit, SendData, SendInitData, BGThreadRunning, ctypes.c_void_p]
        L.ngSpice_Init.restype = ctypes.c_int
        L.ngSpice_Command.argtypes = [ctypes.c_char_p]
        L.ngSpice_Command.restype = ctypes.c_int
        L.ngSpice_Circ.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
        L.ngSpice_Circ.restype = ctypes.c_int
        L.ngGet_Vec_Info.argtypes = [ctypes.c_char_p]
        L.ngGet_Vec_Info.restype = ctypes.POINTER(VectorInfo)
        L.ngSpice_CurPlot.argtypes = []
        L.ngSpice_CurPlot.restype = ctypes.c_char_p
        L.ngSpice_AllVecs.argtypes = [ctypes.c_char_p]
        L.ngSpice_AllVecs.restype = ctypes.POINTER(ctypes.c_char_p)
        L.ngSpice_running.argtypes = []
        L.ngSpice_running.restype = ctypes.c_bool
        L.ngSpice_SetBkpt.argtypes = [ctypes.c_double]
        L.ngSpice_SetBkpt.restype = ctypes.c_bool
        try:
            L.ngSpice_Init_Sync.argtypes = [GetVSRCData, GetISRCData, GetSyncData, ctypes.POINTER(ctypes.c_int), ctypes.c_void_p]
            L.ngSpice_Init_Sync.restype = ctypes.c_int
        except AttributeError:
            pass
        try:
            L.ngSpice_Reset.argtypes = []
            L.ngSpice_Reset.restype = ctypes.c_int
        except AttributeError:
            pass

    def init(self, userdata=None):
        def _sendchar(msg, ident, ud):
            try:
                self.log_lines.append((time.time(), ident, msg.decode(errors="replace")))
            except Exception:
                pass
            return 0
        def _sendstat(msg, ident, ud):
            try:
                self.stat_lines.append((time.time(), ident, msg.decode(errors="replace")))
            except Exception:
                pass
            return 0
        def _exit(status, immediate, quit_, ident, ud):
            self.log_lines.append((time.time(), ident, f"EXIT status={status} quit={bool(quit_)}"))
            return 0
        def _senddata(pvecs, n, ident, ud):
            self.senddata_n += 1
            return 0
        def _sendinit(pvecs, n, ud):
            return 0
        def _bg(run, ident, ud):
            return 0
        cbs = [SendChar(_sendchar), SendStat(_sendstat), ControlledExit(_exit),
               SendData(_senddata), SendInitData(_sendinit), BGThreadRunning(_bg)]
        self._cbs.extend(cbs)
        return self.lib.ngSpice_Init(cbs[0], cbs[1], cbs[2], cbs[3], cbs[4], cbs[5], None)

    def command(self, s):
        t0 = time.perf_counter()
        rc = self.lib.ngSpice_Command(s.encode())
        self.t_spice += time.perf_counter() - t0
        return rc

    def circ(self, lines):
        arr = (ctypes.c_char_p * (len(lines) + 1))(*[l.encode() for l in lines], None)
        t0 = time.perf_counter()
        rc = self.lib.ngSpice_Circ(arr)
        self.t_spice += time.perf_counter() - t0
        return rc

    def vec(self, name):
        p = self.lib.ngGet_Vec_Info(name.encode())
        if not p:
            return None
        vi = p.contents
        n = vi.v_length
        if n <= 0 or not vi.v_realdata:
            return []
        return [vi.v_realdata[i] for i in range(n)]

    def vec_len(self, name):
        p = self.lib.ngGet_Vec_Info(name.encode())
        if not p:
            return -1
        return p.contents.v_length

    def measure_time_vec(self):
        # current plot time vector
        for cand in ("tran1.time", "time"):
            v = self.vec(cand)
            if v:
                return v
        return []
