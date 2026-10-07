"""A same-machine QPC deadline, independent of worker construction times.

No UTC conversion, relative restart or authority claim. Consumers must carry
the originally bound end tick and independently verify the native frequency.
Results are accepted cooperatively; native latency is not forcibly bounded.
"""
import ctypes
import os


class Deadline:
    def __init__(self,end_tick,frequency):
        if os.name!='nt':raise OSError('publish_deadline_windows_required')
        if (type(end_tick) is not int or not 0<end_tick<2**63
                or type(frequency) is not int or not 0<frequency<2**63):
            raise ValueError('publish_deadline_config')
        self._api=ctypes.WinDLL('kernel32',use_last_error=True)
        for name in ('QueryPerformanceCounter','QueryPerformanceFrequency'):
            function=getattr(self._api,name)
            function.argtypes=[ctypes.POINTER(ctypes.c_int64)];function.restype=ctypes.c_int
        actual=ctypes.c_int64()
        if not self._api.QueryPerformanceFrequency(ctypes.byref(actual)) or actual.value!=frequency:
            raise ValueError('publish_deadline_frequency')
        self._end=end_tick;self._frequency=frequency;self._last=None;self._faulted=False
        now=self._read()
        if not 1<end_tick-now<=21600*frequency:
            raise ValueError('publish_deadline_config')
        self._last=now
        self.check()

    @property
    def binding(self):return (self._end,self._frequency)

    def _read(self):
        value=ctypes.c_int64()
        if not self._api.QueryPerformanceCounter(ctypes.byref(value)) or value.value<0:
            raise ValueError('publish_deadline_counter')
        return value.value

    def check(self):
        if self._faulted:raise ValueError('publish_deadline_faulted')
        try:
            now=self._read()
            if self._last is not None and now<self._last:
                raise ValueError('publish_deadline_regressed')
            self._last=now
            # Different-process QPC observations have one-tick order ambiguity.
            if now>=self._end-1:raise TimeoutError('publish_deadline_expired')
        except BaseException:
            self._faulted=True
            raise
