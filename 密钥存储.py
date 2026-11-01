"""Windows DPAPI：密钥只可由当前 Windows 用户解密。"""
import base64
import ctypes as c
import json
from pathlib import Path

FILE = Path(__file__).with_name('密钥.dat')
class Blob(c.Structure):
    _fields_ = [('size', c.c_ulong), ('data', c.POINTER(c.c_ubyte))]

def protect(data, decrypt=False):
    buffer = c.create_string_buffer(data)
    source = Blob(len(data), c.cast(buffer, c.POINTER(c.c_ubyte)))
    target = Blob()
    api = c.WinDLL('crypt32', use_last_error=True)
    function = api.CryptUnprotectData if decrypt else api.CryptProtectData
    if not function(c.byref(source), None, None, None, None, 1, c.byref(target)):
        raise c.WinError(c.get_last_error())
    try:
        return c.string_at(target.data, target.size)
    finally:
        free = c.windll.kernel32.LocalFree
        free.argtypes = [c.c_void_p]
        free(c.cast(target.data, c.c_void_p))

def read(provider):
    if not FILE.exists():
        return ''
    values = json.loads(protect(FILE.read_bytes(), True).decode('utf-8'))
    return values.get(provider, '')

def save(provider, key):
    if not key.strip():
        return
    values = json.loads(protect(FILE.read_bytes(), True).decode('utf-8')) if FILE.exists() else {}
    values[provider] = key.strip()
    temp = FILE.with_suffix('.tmp')
    temp.write_bytes(protect(json.dumps(values).encode('utf-8')))
    temp.replace(FILE)
