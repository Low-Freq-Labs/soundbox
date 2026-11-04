"""Provider deadlines/cancellation use fake clocks, streams and transports only."""
from contextlib import contextmanager
import io
import json
from array import array
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from soundbox.providers import provider_operation
from soundbox_personal import discovery
from soundbox_personal import operations


class ProviderOperations(unittest.TestCase):
    def setUp(self):
        self.now=0.0
        self.clock=patch.object(operations.time,'monotonic',side_effect=lambda:self.now)
        self.clock.start();self.addCleanup(self.clock.stop)

    def test_optional_scope_keeps_public_provider_and_outer_deadline(self):
        with provider_operation(SimpleNamespace(),lambda:True):
            pass
        with self.assertRaises(operations.ProviderDeadline), operations.operation_scope(timeout=5):
            self.now=4
            with operations.operation_scope(timeout=60):
                self.assertEqual(operations.request_timeout(15),1)
            self.now=5
            operations.checkpoint()
        operations.checkpoint()  # scope always restores thread-local state

    def stream(self, *, cancel=None, tick=0):
        outer=self
        class Stream:
            headers=SimpleNamespace(get_content_type=lambda:'image/jpeg')
            def __init__(self):
                self.timeouts=[];self.sizes=[];self.closed=False
                self.fp=SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=self.timeouts.append)))
            def __enter__(self):return self
            def __exit__(self,*_args):self.close()
            def close(self):self.closed=True
            def read(self,_size):raise AssertionError('Large filling read must not be used')
            def read1(self,size):
                self.sizes.append(size);outer.now+=tick
                if cancel is not None:cancel.append(True)
                return b'one trickle chunk'
        return Stream()

    def test_trickle_stream_rechecks_budget_and_socket_timeout_without_hidden_large_read(self):
        stream=self.stream(tick=2)
        with patch.object(discovery.urllib.request,'urlopen',return_value=stream) as opened:
            with self.assertRaises(operations.ProviderDeadline), operations.operation_scope(timeout=5):
                discovery._download('https://example.invalid/image')
        self.assertTrue(stream.closed)
        self.assertEqual(stream.timeouts,[5,3,1])
        self.assertEqual(len(stream.sizes),3)
        self.assertTrue(all(size<=64*1024 for size in stream.sizes))
        self.assertEqual(opened.call_args.kwargs['timeout'],5)

    def test_cancellation_during_chunk_closes_stream_and_stops_next_request(self):
        stopped=[];stream=self.stream(cancel=stopped)
        with patch.object(discovery.urllib.request,'urlopen',return_value=stream) as opened:
            with self.assertRaises(operations.ProviderCancelled), operations.operation_scope(lambda:bool(stopped)):
                discovery.fetch_thumb('https://example.invalid/image')
        self.assertTrue(stream.closed)
        self.assertEqual(len(stream.sizes),1)
        self.assertEqual(opened.call_count,1)

    def test_fallback_stream_reads_one_byte_and_extractor_proxy_preserves_metadata(self):
        data=io.BytesIO(b'abcdef')
        sizes=[]
        def read(size):sizes.append(size);return data.read(size)
        response=SimpleNamespace(read=read,close=data.close,url='https://example.invalid/',headers={'fixture':'yes'})
        proxy=operations.ScopedResponse(response)
        with operations.operation_scope():
            self.assertEqual(proxy.read(2),b'ab')
            self.assertEqual(proxy.read(1),b'c')
            self.assertEqual(proxy.read(),b'def')
        self.assertTrue(data.closed)
        self.assertEqual(set(sizes),{1})
        self.assertEqual(proxy.url,response.url)
        self.assertIs(proxy.headers,response.headers)

    def test_adapter_transform_is_preserved_even_when_its_underlying_fp_has_read1(self):
        class TransformingAdapter:
            def __init__(self):self.fp=io.BytesIO(b'encoded bytes');self.reads=[]
            def read(self,size):
                self.reads.append(size)
                return self.fp.read(size).upper()
            def close(self):self.fp.close()
        response=TransformingAdapter()
        with operations.operation_scope():
            self.assertEqual(operations.ScopedResponse(response).read(),b'ENCODED BYTES')
        self.assertTrue(response.fp.closed)
        self.assertEqual(set(response.reads),{1})

    def test_all_extractor_read_entrypoints_check_cancellation_and_close(self):
        calls=(lambda stream:stream.read1(4),lambda stream:stream.readinto(bytearray(4)),
               lambda stream:next(iter(stream)),lambda stream:stream.readlines())
        for read in calls:
            with self.subTest(read=read):
                response=io.BytesIO(b'fixture\nsecond\n')
                stream=operations.ScopedResponse(response)
                stopped=[]
                with operations.operation_scope(lambda:bool(stopped)):
                    stopped.append(True)
                    with self.assertRaises(operations.ProviderCancelled):read(stream)
                    self.assertTrue(response.closed)
                    stopped.clear()

    def test_fallback_readinto_uses_writable_byte_capacity_and_invalid_buffers_consume_nothing(self):
        for target in (bytearray(8),array('I',[0,0])):
            data=io.BytesIO(b'abcdefgh')
            response=SimpleNamespace(read=data.read,close=data.close)
            with operations.operation_scope():
                count=operations.ScopedResponse(response).readinto(target)
            self.assertEqual(count,8)
            self.assertEqual(memoryview(target).cast('B').tobytes(),b'abcdefgh')
        for target in (b'readonly',memoryview(bytearray(8))[::2]):
            for method in ('readinto','readinto1'):
                with self.subTest(method=method,target=type(target)):
                    data=io.BytesIO(b'fixture')
                    response=SimpleNamespace(read=data.read,close=data.close)
                    with operations.operation_scope():
                        with self.assertRaises(TypeError):getattr(operations.ScopedResponse(response),method)(target)
                    self.assertEqual(data.tell(),0)
                    self.assertFalse(data.closed)

    def test_extractor_updates_each_request_budget_and_returns_exact_json_without_network(self):
        import yt_dlp
        from yt_dlp.networking.common import Request
        outer=self;seen=[];original=Request('https://example.invalid/',extensions={'fixture':1})
        class Extractor:
            def __init__(self,options):self.options=options
            def __enter__(self):return self
            def __exit__(self,*_args):pass
            def urlopen(self,request):
                seen.append(dict(request.extensions))
                stream=io.BytesIO(b'{"entries": []}')
                return SimpleNamespace(fp=stream,read=stream.read,close=stream.close)
            def extract_info(self,_url,download=False):
                first=json.loads(self.urlopen(original).read());outer.now+=3
                second=json.loads(self.urlopen(original).read());assert first==second
                return first
        with patch.object(yt_dlp,'YoutubeDL',Extractor):
            with operations.operation_scope(timeout=5):
                self.assertEqual(discovery._flat_search('fixture'),[])
        self.assertEqual([row['timeout'] for row in seen],[5,2])
        self.assertEqual(original.extensions,{'fixture':1})

    def test_nested_search_fallbacks_share_one_deadline_and_timeout_is_retryable(self):
        queries=[]
        def search(query,_n):queries.append(query);self.now+=2;return []
        with patch.object(discovery,'_liked_candidates',return_value=[]), \
                patch.object(discovery,'_flat_search',side_effect=search), \
                patch.object(discovery,'_download',side_effect=AssertionError('Unexpected download')):
            with self.assertRaises(operations.ProviderDeadline), operations.operation_scope(timeout=5):
                discovery.fetch_best('Fixture Artist','Song (Fixture Remix)')
        self.assertEqual(len(queries),3)

    def test_caller_scope_is_entered_once_and_failures_are_not_returned_as_misses(self):
        events=[]
        @contextmanager
        def scope(stop, *, timeout):
            events.append(('enter',timeout))
            with operations.operation_scope(stop,timeout=timeout):yield
            events.append(('exit',timeout))
        provider=SimpleNamespace(operation_scope=scope)
        with self.assertRaises(operations.ProviderCancelled):
            with provider_operation(provider,lambda:True):
                self.fail('Cancelled operation started')
        self.assertEqual(events,[('enter',60.0)])
