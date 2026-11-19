"""Observable JSON containers for revision-bound worker validation.

Use ordinary instance methods/operators. Deliberately invoking unbound builtin
mutators bypasses Python subclass dispatch and is not a supported mutation API.
Snapshots are detached plain JSON; odd revisions always mean mutation in flight.
"""
from __future__ import annotations

from contextlib import contextmanager
import math
import threading
from typing import Any
import weakref


class MutationChanged(ValueError):
    """A worker must retry rather than validate a mixed or changing snapshot."""


def _immutable(*args,**kwargs):
    raise TypeError('Frozen JSON snapshots cannot be mutated')


class _Frozen:
    __slots__=()

    def __deepcopy__(self,memo):return self


class FrozenDict(_Frozen,dict):
    """Deeply immutable JSON map; normal instance mutation is unsupported."""
    __slots__=()
    __init__=__setitem__=__delitem__=clear=pop=popitem=setdefault=update=__ior__=_immutable


class FrozenList(_Frozen,list):
    """Deeply immutable JSON list, intentionally retaining list's unhashability."""
    __slots__=()
    __init__=__setitem__=__delitem__=append=extend=insert=pop=remove=clear=reverse=sort=__iadd__=__imul__=_immutable


_GRAPH_LOCK=threading.RLock()


class _Revision:
    __slots__=('value','active','parents','__weakref__')

    def __init__(self):
        self.value=0;self.active=0
        self.parents:weakref.ReferenceType[_Revision]|tuple[weakref.ReferenceType[_Revision],...]|None=None

    def add_parent(self,parent):
        # Plain no-callback weakrefs are cached by Python: all of a parent's
        # children share one ref. Most JSON nodes have exactly one parent, so
        # they need neither a collection nor a callback/function object.
        reference=weakref.ref(parent);existing=self.parents
        live:tuple[weakref.ReferenceType[_Revision],...]
        if existing is None:self.parents=reference;return
        if isinstance(existing,weakref.ReferenceType):
            if existing() is parent:return
            live=() if existing() is None else (existing,)
        else:
            live=tuple(value for value in existing if value() is not None)
            if any(value() is parent for value in live):
                self.parents=live[0] if len(live)==1 else live
                return
        self.parents=(*live,reference) if live else reference

    def ancestry(self):
        pending=[self];result=[];seen=set()
        while pending:
            node=pending.pop()
            if node in seen:continue
            seen.add(node);result.append(node)
            parents=node.parents
            references=parents if isinstance(parents,tuple) else (() if parents is None else (parents,))
            pending.extend(parent for reference in references if (parent:=reference()) is not None)
        return result


@contextmanager
def _changing(value):
    with _GRAPH_LOCK:
        nodes=value._revision_node.ancestry()
        for node in nodes:
            if not node.active:node.value+=1
            node.active+=1
    try:yield
    finally:
        with _GRAPH_LOCK:
            for node in reversed(nodes):
                node.active-=1
                if not node.active:node.value+=1


def _key(value):
    if type(value) is not str:raise ValueError('Observed JSON dictionary keys must be text')
    return value


def _attach(value,parent):
    if isinstance(value,(ObservedDict,ObservedList)):
        node=value._revision_node
        with _GRAPH_LOCK:
            if node.value%2:raise MutationChanged('Observed JSON is changing or cyclic')
            node.add_parent(parent._revision_node)
    return value


def _build(value,stack,memo,parent=None):
    if isinstance(value,(ObservedDict,ObservedList)):
        return _attach(value,parent) if parent is not None else value
    if value is None or type(value) in (str,bool,int):return value
    if type(value) is float:
        if not math.isfinite(value):raise ValueError('Observed JSON numbers must be finite')
        return value
    if not isinstance(value,(dict,list)):raise ValueError('Unsupported observed JSON value')
    identity=id(value)
    if identity in stack:raise ValueError('Cyclic observed JSON is not supported')
    if identity in memo:
        result=memo[identity]
        return _attach(result,parent) if parent is not None else result
    result=ObservedDict._empty() if isinstance(value,dict) else ObservedList._empty()
    memo[identity]=result;stack.add(identity)
    try:
        if isinstance(value,dict):
            for key,child in dict.items(value):dict.__setitem__(result,_key(key),_build(child,stack,memo,result))
        else:
            for child in list.__iter__(value):list.append(result,_build(child,stack,memo,result))
    finally:stack.remove(identity)
    return _attach(result,parent) if parent is not None else result


def _plain(value,memo):
    if not isinstance(value,(dict,list)):return value
    identity=id(value)
    if identity in memo:return memo[identity]
    result:Any={} if isinstance(value,dict) else []
    memo[identity]=result
    if isinstance(value,dict):
        for key,child in dict.items(value):result[key]=_plain(child,memo)
    else:
        for child in list.__iter__(value):result.append(_plain(child,memo))
    return result


def _freeze(value,memo):
    if isinstance(value,(FrozenDict,FrozenList)):return value
    if not isinstance(value,(dict,list)):return value
    identity=id(value)
    if identity in memo:return memo[identity]
    result:Any=dict.__new__(FrozenDict) if isinstance(value,dict) else list.__new__(FrozenList)
    memo[identity]=result
    if isinstance(value,dict):
        for key,child in dict.items(value):dict.__setitem__(result,key,_freeze(child,memo))
    else:
        for child in list.__iter__(value):list.append(result,_freeze(child,memo))
    return result


class _Observed:
    _revision_node:_Revision
    @property
    def revision(self):return self._revision_node.value

    def snapshot(self):
        before=self.revision
        if before%2:raise MutationChanged('Observed JSON mutation is in progress')
        try:result=_plain(self,{})
        except RuntimeError as exc:raise MutationChanged('Observed JSON changed during snapshot') from exc
        if before!=self.revision or self.revision%2:raise MutationChanged('Observed JSON changed during snapshot')
        return result

    def __deepcopy__(self,memo):
        result=self.snapshot();memo[id(self)]=result;return result

    def frozen_snapshot(self):
        """Reuse immutable detached JSON only for this exact recursive revision.

        The first snapshot belongs on a worker. Later worker semantic passes
        may share it safely; caller-owned mutable progress still detaches.
        """
        before=self.revision
        if before%2:raise MutationChanged('Observed JSON mutation is in progress')
        cached=getattr(self,'_frozen_cache',None)
        if cached is not None and cached[0]==before and self.revision==before:return cached[1]
        try:result=_freeze(self,{})
        except RuntimeError as exc:raise MutationChanged('Observed JSON changed during snapshot') from exc
        if before!=self.revision or self.revision%2:raise MutationChanged('Observed JSON changed during snapshot')
        self._frozen_cache=(before,result)
        return result


class ObservedDict(_Observed,dict):
    @classmethod
    def _empty(cls):
        result=dict.__new__(cls);dict.__init__(result);result._revision_node=_Revision();return result

    def __init__(self,*args,**kwargs):
        if not hasattr(self,'_revision_node'):self._revision_node=_Revision()
        with _changing(self):
            incoming=dict(*args,**kwargs);prepared=_build(incoming,set(),{})
            dict.clear(self)
            for key,value in dict.items(prepared):dict.__setitem__(self,key,_attach(value,self))

    def __setitem__(self,key,value):
        with _changing(self):dict.__setitem__(self,_key(key),_build(value,set(),{},self))

    def __delitem__(self,key):
        with _changing(self):dict.__delitem__(self,key)

    def clear(self):
        with _changing(self):dict.clear(self)

    def pop(self,key,*default):
        with _changing(self):return dict.pop(self,key,*default)

    def popitem(self):
        with _changing(self):return dict.popitem(self)

    def setdefault(self,key,default=None):
        _key(key)
        if key in self:return dict.__getitem__(self,key)
        with _changing(self):
            value=_build(default,set(),{},self);dict.__setitem__(self,key,value);return value

    def update(self,*args,**kwargs):
        with _changing(self):
            incoming=dict(*args,**kwargs);prepared=_build(incoming,set(),{})
            for key,value in dict.items(prepared):dict.__setitem__(self,key,_attach(value,self))

    def __ior__(self,other:Any,/)->Any:self.update(other);return self

    def __or__(self,other:Any,/)->Any:return dict.__or__(self,other)


class ObservedList(_Observed,list):
    @classmethod
    def _empty(cls):
        result=list.__new__(cls);list.__init__(result);result._revision_node=_Revision();return result

    def __init__(self,values=()):
        if not hasattr(self,'_revision_node'):self._revision_node=_Revision()
        with _changing(self):
            prepared=_build(list(values),set(),{})
            list.clear(self)
            list.extend(self,[_attach(value,self) for value in list.__iter__(prepared)])

    def __setitem__(self,index,value):
        with _changing(self):
            if isinstance(index,slice):
                prepared=_build(list(value),set(),{})
                list.__setitem__(self,index,[_attach(child,self) for child in list.__iter__(prepared)])
            else:list.__setitem__(self,index,_build(value,set(),{},self))

    def __delitem__(self,index):
        with _changing(self):list.__delitem__(self,index)

    def append(self,value):
        with _changing(self):list.append(self,_build(value,set(),{},self))

    def extend(self,values):
        with _changing(self):
            prepared=_build(list(values),set(),{})
            list.extend(self,[_attach(value,self) for value in list.__iter__(prepared)])

    def insert(self,index,value):
        with _changing(self):list.insert(self,index,_build(value,set(),{},self))

    def pop(self,index=-1):
        with _changing(self):return list.pop(self,index)

    def remove(self,value):
        with _changing(self):list.remove(self,value)

    def clear(self):
        with _changing(self):list.clear(self)

    def reverse(self):
        with _changing(self):list.reverse(self)

    def sort(self,*args,**kwargs):
        with _changing(self):list.sort(self,*args,**kwargs)

    def __iadd__(self,values:Any,/)->Any:self.extend(values);return self

    def __add__(self,values:Any,/)->Any:return list.__add__(self,values)

    def __mul__(self,count:Any,/)->Any:return list.__mul__(self,count)

    def __imul__(self,count:Any,/)->Any:
        with _changing(self):list.__imul__(self,count)
        return self


def observe(value):
    """Detach raw aliases; retain already-observed descendants with parent stamps."""
    return _build(value,set(),{})
