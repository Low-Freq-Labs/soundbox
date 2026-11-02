"""Persistent Finder/Dock artwork through macOS custom-icon metadata.

NSWorkspace owns this format. Never rewrite Contents or the signed ICNS asset,
and never change another application's icon from a development Python process.
"""
import ctypes
from contextlib import contextmanager
from pathlib import Path
import sys


def application_bundle():
    if sys.platform != 'darwin' or not getattr(sys, 'frozen', False):
        return None
    executable = Path(sys.executable).absolute()
    if (executable.name != 'Soundbox' or executable.parent.name != 'MacOS'
            or executable.parent.parent.name != 'Contents'):
        return None
    bundle = executable.parents[2]
    if bundle.suffix != '.app' or bundle.resolve(strict=True) != bundle:
        return None
    return bundle


@contextmanager
def _native_image(png):
    if sys.platform != 'darwin':
        raise OSError('Persistent application icons require macOS')
    if not isinstance(png, bytes) or not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png) > 8*1024*1024:
        raise ValueError('Persistent application icon requires bounded PNG data')
    ctypes.CDLL('/System/Library/Frameworks/AppKit.framework/AppKit')
    objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    pointer = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = pointer
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = pointer
    address = ctypes.cast(objc.objc_msgSend, pointer).value
    if address is None:
        raise OSError('macOS icon service is unavailable')
    def send(receiver, selector, result=pointer, types=(), args=()):
        call = ctypes.CFUNCTYPE(result, pointer, pointer, *types)(address)
        return call(receiver, objc.sel_registerName(selector.encode()), *args)
    def cls(name):
        return objc.objc_getClass(name.encode())
    pool = send(cls('NSAutoreleasePool'), 'new')
    image = None
    try:
        raw = ctypes.create_string_buffer(png)
        data = send(cls('NSData'), 'dataWithBytes:length:', types=(pointer, ctypes.c_ulong), args=(raw,len(png)))
        image = send(send(cls('NSImage'), 'alloc'), 'initWithData:', types=(pointer,), args=(data,))
        if not image:
            raise OSError('macOS could not decode the saved application icon')
        yield send,cls,image
    finally:
        if image:
            send(image, 'release', None)
        send(pool, 'drain', None)


def set_custom_icon(bundle, png):
    """Set OS-managed metadata, retaining every signed Contents byte."""
    bundle=Path(bundle)
    if (bundle.suffix!='.app' or not bundle.is_dir() or bundle.resolve(strict=True)!=bundle
            or not (bundle/'Contents/Info.plist').is_file()):raise OSError('Persistent icon requires a canonical application bundle')
    with _native_image(png) as (send,cls,image):
        path=send(cls('NSString'),'stringWithUTF8String:',types=(ctypes.c_char_p,),args=(str(bundle).encode(),))
        workspace=send(cls('NSWorkspace'),'sharedWorkspace')
        if not send(workspace,'setIcon:forFile:options:',ctypes.c_bool,
                    (ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong),(image,path,0)):
            raise OSError('macOS could not persist the saved application icon')


def refresh_running_icon(png):
    """Refresh AppKit's actual Dock image after launch/OK, not Qt's cached icon."""
    with _native_image(png) as (send,cls,image):
        app=send(cls('NSApplication'),'sharedApplication')
        send(app,'setApplicationIconImage:',None,(ctypes.c_void_p,),(image,))
        tile=send(app,'dockTile');send(tile,'display',None)
