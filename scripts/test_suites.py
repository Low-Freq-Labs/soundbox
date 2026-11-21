"""Static, composable test-area plans; no test modules or Qt are imported."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
LEVELS=('quick','integration','recovery')


def normalize_selector(selector,root=ROOT):
    """Accept the focused runner's module/path/class/method names without loading."""
    if not isinstance(selector,str):raise ValueError('Test selectors must be strings')
    name=selector
    if name.startswith('tests/') and name.endswith('.py'):
        name=name[6:-3]
    elif name.startswith('tests.'):
        name=name[6:]
    parts=name.split('.')
    if (not all(part.isidentifier() for part in parts) or not parts[0].startswith('test_')
            or not (Path(root)/'tests'/f'{parts[0]}.py').is_file()):
        raise ValueError(f'Use a test module, class, method or tests/test_*.py path: {selector}')
    return name


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError(f'Duplicate catalog key: {key}')
        result[key]=value
    return result


def _catalog_reference(selector,root,trees):
    parts=selector.split('.')
    if len(parts)==1:return
    module=parts[0]
    if module not in trees:
        path=Path(root)/'tests'/f'{module}.py'
        try:trees[module]=ast.parse(path.read_text(),filename=str(path))
        except (OSError,UnicodeError,SyntaxError) as exc:
            raise ValueError(f'Cannot inspect catalog test module {module}: {exc}') from exc
    nodes=trees[module].body
    for index,part in enumerate(parts[1:]):
        allowed=((ast.ClassDef,) if index==0 or index<len(parts)-2 else
                 (ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef))
        matches=[node for node in nodes if isinstance(node,allowed) and node.name==part]
        if len(matches)!=1:raise ValueError(f'Unknown static catalog selector: {selector}')
        nodes=matches[0].body


def load_catalog(root=ROOT):
    """Validate configuration and static references, retaining no executable state."""
    path=Path(root)/'scripts/test-suites.json'
    try:document=json.loads(path.read_text(),object_pairs_hook=_object)
    except (OSError,UnicodeError,json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot read test suite catalog: {exc}') from exc
    if (not isinstance(document,dict) or set(document)!={'version','suites'}
            or type(document['version']) is not int or document['version']!=1):
        raise ValueError('Expected test suite catalog version 1 and suites only')
    suites=document['suites']
    if not isinstance(suites,dict) or not suites:raise ValueError('Test suite catalog must contain named suites')
    normalized={};trees={}
    for name,suite in suites.items():
        if not isinstance(name,str) or re.fullmatch(r'[a-z][a-z0-9-]*',name) is None:
            raise ValueError(f'Invalid test suite name: {name}')
        if not isinstance(suite,dict) or set(suite)!={'description',*LEVELS}:
            raise ValueError(f'Suite {name} requires description and exactly these levels: {", ".join(LEVELS)}')
        description=suite['description']
        if not isinstance(description,str) or not description.strip():
            raise ValueError(f'Suite {name} needs a description')
        values={'description':description.strip()};seen=set()
        for level in LEVELS:
            selectors=suite[level]
            if not isinstance(selectors,list) or level=='quick' and not selectors:
                raise ValueError(f'Suite {name} needs selector lists and nonempty quick coverage')
            values[level]=[]
            for selector in selectors:
                canonical=normalize_selector(selector,root)
                if canonical in seen:raise ValueError(f'Duplicate selector in suite {name}: {canonical}')
                _catalog_reference(canonical,root,trees)
                seen.add(canonical);values[level].append(canonical)
        normalized[name]=values
    return normalized


def suite_plan(names,level='quick',selectors=(),cocoa=False,root=ROOT):
    """Union cumulative area levels and exact selectors, then choose Qt processes."""
    if level not in LEVELS:raise ValueError(f'Unknown test level: {level}')
    if isinstance(names,(str,bytes)) or isinstance(selectors,(str,bytes)):
        raise ValueError('Suite names and selectors must be sequences of strings')
    requested=[]
    for name in names:
        if not isinstance(name,str):raise ValueError('Suite names must be strings')
        if name not in requested:requested.append(name)
    catalog=load_catalog(root) if requested else {}
    chosen=set()
    for name in requested:
        if name not in catalog:raise ValueError(f'Unknown test suite: {name}')
        for included in LEVELS[:LEVELS.index(level)+1]:chosen.update(catalog[name][included])
    chosen.update(normalize_selector(selector,root) for selector in selectors)
    if not chosen:raise ValueError('Select at least one suite or test selector')
    ordered=sorted(chosen);phases={'offscreen':[],'native':[]}
    for selector in ordered:
        phase='native' if cocoa or selector.split('.')[0]=='test_native_deck' else 'offscreen'
        phases[phase].append(selector)
    return dict(suites=requested,level=level,selectors=ordered,phases=phases)
