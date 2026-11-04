"""Real HTTPResponse/pinned adapters over fake SocketIO; never network sockets."""
import gzip
import http.client
import io
import socket
from array import array
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from soundbox_personal import operations


class FakeSocket:
    def __init__(self,payload, *, chunk=4096,on_receive=None,close_error=None):
        self.data=io.BytesIO(payload);self.chunk=chunk;self.on_receive=on_receive
        self.timeout=7.0;self.timeouts=[];self.decrefs=0;self.receives=0;self.close_error=close_error
        self.raw=None
    def gettimeout(self):return self.timeout
    def settimeout(self,value):self.timeout=value;self.timeouts.append(value)
    def _decref_socketios(self):
        self.decrefs+=1
        if self.close_error:raise self.close_error
    def recv_into(self,buffer,*_args):
        self.receives+=1
        if self.on_receive:self.on_receive(self)
        data=self.data.read(min(len(buffer),self.chunk));buffer[:len(data)]=data
        return len(data)
    def makefile(self,*_args):
        self.raw=socket.SocketIO(self,'rb')
        return io.BufferedReader(self.raw)


def http_response(payload, *, encoded=False, content_length=None, **options):
    length=len(payload) if content_length is None else content_length
    headers=(b'Content-Encoding: gzip\r\n' if encoded else b'')
    wire=b'HTTP/1.1 200 OK\r\nContent-Length: '+str(length).encode()+b'\r\n'+headers+b'\r\n'+payload
    fake=FakeSocket(wire,**options)
    response=http.client.HTTPResponse(fake);response.begin();response.url='https://example.invalid/'
    return response,fake


class SocketGuard(unittest.TestCase):
    def test_http_response_bytes_close_refs_and_timeout_equal_without_buffered_byte_overhead(self):
        from yt_dlp.networking._urllib import UrllibResponseAdapter
        data=b'generated JSON response '*16000
        for wrapped in (False,True):
            response,fake=http_response(data)
            adapter=UrllibResponseAdapter(response)
            with operations.operation_scope(timeout=60):
                stream=operations.ScopedResponse(adapter) if wrapped else adapter
                self.assertEqual(stream.read(25),data[:25])
                self.assertEqual(stream.read(),data[25:])
                stream.close()
            self.assertEqual(fake.decrefs,1)
            self.assertIsNone(fake.raw._sock)
            self.assertEqual(fake.gettimeout(),7.0)
            self.assertLess(fake.receives,110)

    def test_requests_adapter_retains_gzip_decoding_and_original_read_method(self):
        import urllib3
        from yt_dlp.networking._requests import RequestsResponseAdapter
        data=b'gzip fixture metadata '*18000
        compressed=gzip.compress(data,mtime=0)
        http,fake=http_response(compressed,encoded=True,chunk=64)
        raw=urllib3.response.HTTPResponse(body=http,headers={'content-encoding':'gzip'},
                                        preload_content=False,decode_content=False,original_response=http)
        response=SimpleNamespace(raw=raw,headers={'content-encoding':'gzip'},url=http.url,status_code=200,reason='OK')
        adapter=RequestsResponseAdapter(response)
        original=adapter.read.__func__
        with operations.operation_scope():
            scoped=operations.ScopedResponse(adapter)
            self.assertIsNotNone(scoped._socket_guard)
            self.assertIs(adapter.read.__func__,original)
            self.assertEqual(scoped.read(21),data[:21])
            self.assertEqual(scoped.read(),data[21:])
        self.assertEqual(fake.decrefs,1)
        self.assertIsNone(fake.raw._sock)
        self.assertEqual(fake.gettimeout(),7.0)

    def test_cancel_and_deadline_checked_per_receive_even_with_one_large_adapter_read(self):
        from yt_dlp.networking._urllib import UrllibResponseAdapter
        for mode in ('cancel','deadline'):
            with self.subTest(mode=mode):
                now=[0.0];stopped=[]
                response,fake=http_response(b'x'*100000,chunk=256)
                before=fake.receives
                def receive(_socket):
                    now[0]+=1
                    if mode=='cancel':stopped.append(True)
                fake.on_receive=receive
                expected=operations.ProviderCancelled if mode=='cancel' else operations.ProviderDeadline
                with patch.object(operations.time,'monotonic',side_effect=lambda:now[0]):
                    with self.assertRaises(expected), operations.operation_scope(lambda:bool(stopped),timeout=2):
                        operations.ScopedResponse(UrllibResponseAdapter(response)).read()
                self.assertLessEqual(fake.receives-before,2)
                self.assertEqual(fake.decrefs,1)
                self.assertIsNone(fake.raw._sock)
                self.assertEqual(fake.gettimeout(),7.0)

    def test_closed_raw_is_not_resurrected_and_primary_errors_survive_cleanup_error(self):
        raw=socket.SocketIO(FakeSocket(b''),'rb');raw.close()
        response=SimpleNamespace(fp=raw,read=lambda _size:b'',close=raw.close)
        scoped=operations.ScopedResponse(response)
        self.assertIsNone(scoped._socket_guard)
        scoped.close();self.assertIsNone(raw._sock)

        fake=FakeSocket(b'',close_error=OSError('cleanup failed'))
        raw=socket.SocketIO(fake,'rb');buffer=io.BufferedReader(raw)
        def broken(_size):raise ValueError('primary read failure')
        response=SimpleNamespace(fp=buffer,read=broken,close=buffer.close)
        scoped=operations.ScopedResponse(response)
        with self.assertRaisesRegex(ValueError,'primary read failure'):
            scoped.read()
        self.assertIs(raw._sock,fake)  # retain CPython's own failed-close state
        self.assertEqual(fake.decrefs,1)
        self.assertEqual(fake.gettimeout(),7.0)

    def test_specialized_incomplete_read_error_type_is_preserved(self):
        from yt_dlp.networking._urllib import UrllibResponseAdapter
        errors=[]
        for wrapped in (False,True):
            response,fake=http_response(b'short',content_length=100)
            adapter=UrllibResponseAdapter(response)
            stream=operations.ScopedResponse(adapter) if wrapped else adapter
            try:stream.read()
            except Exception as exc:errors.append(type(exc))
            finally:stream.close()
            self.assertEqual(fake.decrefs,1)
        self.assertEqual(len(errors),2)
        self.assertEqual(errors[0],errors[1])

    def test_eof_restores_timeout_before_pool_release_and_close_never_touches_next_borrow(self):
        from yt_dlp.networking._urllib import UrllibResponseAdapter
        response,fake=http_response(b'fixture')
        observed=[]
        def release():
            fake.decrefs+=1
            observed.append(fake.timeout)
            fake.timeout=.125  # a later pool borrower owns its timeout now
        fake._decref_socketios=release
        with operations.operation_scope(timeout=3):
            scoped=operations.ScopedResponse(UrllibResponseAdapter(response))
            self.assertEqual(scoped.read(),b'fixture')
        self.assertEqual(observed,[7.0])
        self.assertEqual(fake.timeout,.125)
        self.assertIsNone(fake.raw._sock)
        scoped.close()
        self.assertEqual(fake.timeout,.125)

    def test_guarded_adapter_short_read_keeps_its_transform_and_remaining_stream(self):
        fake=FakeSocket(b'abcdefgh',chunk=4)
        buffer=fake.makefile()
        class Adapter:
            fp=buffer
            def read(self,size=None):
                return (buffer.read1(3) if size is not None else buffer.read()).upper()
            def close(self):buffer.close()
        with operations.operation_scope():
            scoped=operations.ScopedResponse(Adapter())
            self.assertEqual(scoped.read(10),b'ABC')
            self.assertFalse(buffer.closed)
            self.assertEqual(scoped.read(),b'DEFGH')
        self.assertEqual(fake.decrefs,1)

    def test_native_read1_keeps_available_short_read_instead_of_filling(self):
        payload=b'0123456789'*10000
        outputs=[]
        for wrapped in (False,True):
            response,fake=http_response(payload,chunk=32)
            with operations.operation_scope():
                stream=operations.ScopedResponse(response) if wrapped else response
                before=fake.receives
                data=stream.read1(1000)
                self.assertLess(len(data),1000)
                self.assertLessEqual(fake.receives-before,1)
                self.assertEqual(data,payload[:len(data)])
                outputs.append(data)
                stream.close()
            self.assertEqual(fake.decrefs,1)
        self.assertEqual(outputs[0],outputs[1])

    def test_native_readinto_and_lines_preserve_http_stream_contract(self):
        for method in ('readinto','readinto1'):
            for buffer in (bytearray(8),array('I',[0,0])):
                with self.subTest(method=method,buffer=type(buffer)):
                    outputs=[]
                    for wrapped in (False,True):
                        target=bytearray(8) if isinstance(buffer,bytearray) else array('I',[0,0])
                        response,fake=http_response(b'abcdefghi\nnext\n',chunk=32)
                        with operations.operation_scope():
                            stream=operations.ScopedResponse(response) if wrapped else response
                            count=getattr(stream,method)(target)
                            outputs.append((count,memoryview(target).cast('B').tobytes(),stream.readlines()))
                            stream.close()
                    self.assertEqual(outputs[0],outputs[1])

    def test_native_invalid_readinto_buffers_do_not_consume_or_close_the_stream(self):
        for method in ('readinto','readinto1'):
            for target in (b'readonly',memoryview(bytearray(8))[::2]):
                with self.subTest(method=method,target=type(target)):
                    response,fake=http_response(b'fixture')
                    scoped=operations.ScopedResponse(response)
                    before=fake.receives
                    with operations.operation_scope():
                        with self.assertRaises(TypeError):getattr(scoped,method)(target)
                        self.assertEqual(fake.receives,before)
                        self.assertIsNotNone(response.fp)
                        self.assertEqual(scoped.read(),b'fixture')
                    self.assertEqual(fake.decrefs,1)

    def test_invalid_read_sizes_reject_before_consuming_or_closing_http_response(self):
        for method in ('read','read1','readline','readlines'):
            for size in (1.5,'2'):
                with self.subTest(method=method,size=size):
                    response,fake=http_response(b'fixture')
                    scoped=operations.ScopedResponse(response);before=fake.receives
                    with operations.operation_scope():
                        with self.assertRaises(TypeError):getattr(scoped,method)(size)
                        self.assertEqual(fake.receives,before)
                        self.assertIsNotNone(response.fp)
                        self.assertEqual(scoped.read(),b'fixture')
                    self.assertEqual(fake.decrefs,1)
        class IndexedSize:
            def __index__(self):return 3
        response,fake=http_response(b'fixture')
        with operations.operation_scope():
            scoped=operations.ScopedResponse(response)
            self.assertEqual(scoped.read(IndexedSize()),b'fix')
            self.assertEqual(scoped.read(),b'ture')
