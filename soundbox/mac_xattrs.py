"""Bounded Darwin extended attributes for recoverable, confirmed file disposal."""
import ctypes
import errno
import os
import sys

_LIBC=ctypes.CDLL(None,use_errno=True)
if sys.platform=='darwin':
    _LIBC.getxattr.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_int]
    _LIBC.getxattr.restype=ctypes.c_ssize_t
    _LIBC.setxattr.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_int]
    _LIBC.setxattr.restype=ctypes.c_int
    _LIBC.removexattr.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int];_LIBC.removexattr.restype=ctypes.c_int


def _supported():
    if sys.platform!='darwin':raise OSError('Recoverable desktop file disposal requires macOS')


def _error(path):
    code=ctypes.get_errno();return OSError(code,os.strerror(code),str(path))


def get(path,name):
    _supported();raw=os.fsencode(path);key=name.encode()
    size=int(_LIBC.getxattr(raw,key,None,0,0,1))
    if size<0:
        if ctypes.get_errno()==getattr(errno,'ENOATTR',93):return None
        raise _error(path)
    if size>4096:raise ValueError('Disposal extended attribute exceeds its bound')
    buffer=ctypes.create_string_buffer(size)
    count=int(_LIBC.getxattr(raw,key,buffer,size,0,1))
    if count<0:raise _error(path)
    if count!=size:raise OSError('Disposal attribute changed during its read')
    return buffer.raw[:count]


def set(path,name,value):
    _supported()
    if not isinstance(value,bytes) or len(value)>4096:raise ValueError('Invalid disposal attribute')
    buffer=ctypes.create_string_buffer(value)
    if _LIBC.setxattr(os.fsencode(path),name.encode(),buffer,len(value),0,1)!=0:raise _error(path)


def remove(path,name):
    _supported()
    if _LIBC.removexattr(os.fsencode(path),name.encode(),1)!=0 and ctypes.get_errno()!=getattr(errno,'ENOATTR',93):
        raise _error(path)
