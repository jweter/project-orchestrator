"""Fail-closed Windows session detection for SYSTEM-to-user supervisor handoff."""
from __future__ import annotations
import ctypes, os
from ctypes import wintypes

WTS_ACTIVE=0
WTS_CONNECTED=1
WTS_USERNAME=5

class WTS_SESSION_INFO(ctypes.Structure):
    _fields_=[("SessionId",wintypes.DWORD),("pWinStationName",wintypes.LPWSTR),("State",ctypes.c_int)]

def active_interactive_sessions()->list[dict]:
    if os.name!="nt":
        return []
    wts=ctypes.WinDLL("wtsapi32",use_last_error=True)
    pp=ctypes.POINTER(WTS_SESSION_INFO)()
    count=wintypes.DWORD()
    if not wts.WTSEnumerateSessionsW(0,0,1,ctypes.byref(pp),ctypes.byref(count)):
        raise OSError(ctypes.get_last_error(),"WTSEnumerateSessionsW failed")
    sessions=[]
    try:
        for i in range(count.value):
            item=pp[i]
            if item.State not in (WTS_ACTIVE,WTS_CONNECTED):
                continue
            buf=wintypes.LPWSTR(); size=wintypes.DWORD()
            if not wts.WTSQuerySessionInformationW(0,item.SessionId,WTS_USERNAME,
                                                    ctypes.byref(buf),ctypes.byref(size)):
                raise OSError(ctypes.get_last_error(),"WTSQuerySessionInformationW failed")
            try:
                username=buf.value or ""
            finally:
                if buf:
                    wts.WTSFreeMemory(buf)
            if username:
                sessions.append({"session_id":int(item.SessionId),
                                 "station":item.pWinStationName or "",
                                 "state":int(item.State),"username":username})
    finally:
        wts.WTSFreeMemory(pp)
    return sessions

def boot_owner_should_yield(sessions:list[dict])->bool:
    """Any authenticated active/connected interactive session transfers ownership."""
    return any(str(s.get("username","")).strip() and s.get("state") in (WTS_ACTIVE,WTS_CONNECTED)
               for s in sessions)
