"""Windows WLAN profile read. Credentials stay in process memory."""
import ctypes
from ctypes import wintypes as wt
import xml.etree.ElementTree as ET
from uuid import UUID

class GUID(ctypes.Structure):
    _fields_=[('Data1',wt.DWORD),('Data2',wt.WORD),('Data3',wt.WORD),('Data4',ctypes.c_ubyte*8)]
class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_=[('guid',GUID),('description',wt.WCHAR*256),('state',ctypes.c_int)]
class WLAN_INTERFACE_LIST_HEADER(ctypes.Structure):
    _fields_=[('count',wt.DWORD),('index',wt.DWORD)]

def connected_credentials():
    api=ctypes.WinDLL('wlanapi')
    handle=wt.HANDLE();version=wt.DWORD()
    api.WlanOpenHandle.argtypes=[wt.DWORD,ctypes.c_void_p,ctypes.POINTER(wt.DWORD),ctypes.POINTER(wt.HANDLE)]
    api.WlanEnumInterfaces.argtypes=[wt.HANDLE,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    api.WlanQueryInterface.argtypes=[wt.HANDLE,ctypes.POINTER(GUID),ctypes.c_int,ctypes.c_void_p,ctypes.POINTER(wt.DWORD),ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
    api.WlanGetProfile.argtypes=[wt.HANDLE,ctypes.POINTER(GUID),wt.LPCWSTR,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wt.DWORD),ctypes.POINTER(wt.DWORD)]
    api.WlanFreeMemory.argtypes=[ctypes.c_void_p]
    api.WlanCloseHandle.argtypes=[wt.HANDLE,ctypes.c_void_p]
    rc=api.WlanOpenHandle(2,None,ctypes.byref(version),ctypes.byref(handle))
    if rc:raise OSError(rc,'WlanOpenHandle')
    interfaces=ctypes.c_void_p()
    try:
        rc=api.WlanEnumInterfaces(handle,None,ctypes.byref(interfaces))
        if rc:raise OSError(rc,'WlanEnumInterfaces')
        header=ctypes.cast(interfaces,ctypes.POINTER(WLAN_INTERFACE_LIST_HEADER)).contents
        found=[]
        base=interfaces.value+ctypes.sizeof(WLAN_INTERFACE_LIST_HEADER)
        for index in range(header.count):
            info=WLAN_INTERFACE_INFO.from_address(base+index*ctypes.sizeof(WLAN_INTERFACE_INFO))
            if info.state==1:found.append(info)
        if len(found)!=1:raise RuntimeError('Expected one connected WLAN interface')
        info=found[0]
        current=ctypes.c_void_p();size=wt.DWORD()
        # wlan_intf_opcode_current_connection == 7. Profile follows state + mode.
        rc=api.WlanQueryInterface(handle,ctypes.byref(info.guid),7,None,ctypes.byref(size),ctypes.byref(current),None)
        if rc:raise OSError(rc,'WlanQueryInterface')
        try:profile=ctypes.wstring_at(current.value+8,256).split('\0')[0]
        finally:api.WlanFreeMemory(current)
        flags=wt.DWORD(4);access=wt.DWORD();xml=ctypes.c_void_p()
        rc=api.WlanGetProfile(handle,ctypes.byref(info.guid),profile,None,ctypes.byref(xml),ctypes.byref(flags),ctypes.byref(access))
        if rc:raise OSError(rc,'WlanGetProfile')
        try:
            root=ET.fromstring(ctypes.wstring_at(xml))
            ns={'w':'http://www.microsoft.com/networking/WLAN/profile/v1'}
            ssid=root.findtext('w:SSIDConfig/w:SSID/w:name',namespaces=ns)
            protected=root.findtext('w:MSM/w:security/w:sharedKey/w:protected',namespaces=ns)
            key=root.findtext('w:MSM/w:security/w:sharedKey/w:keyMaterial',namespaces=ns)
            if protected!='false' or not key:raise RuntimeError('Wi-Fi profile key unavailable in plaintext')
            return ssid,key
        finally:api.WlanFreeMemory(xml)
    finally:
        if interfaces:api.WlanFreeMemory(interfaces)
        api.WlanCloseHandle(handle,None)

if __name__=='__main__':
    ssid,key=connected_credentials()
    print('Connected WLAN profile read; compatible key length:',8<=len(key)<=63)
