"""Check authored-folder guides and static dependency directions, without app I/O."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
VENDOR = 'native/cdj/vendor/'
PRODUCTION = {'soundbox', 'soundbox_personal', 'desktop'}
GUIDE_LINES = 25


def authored_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=root, check=True, capture_output=True,
    )
    return sorted({Path(name.decode()) for name in result.stdout.split(b'\0')
                   if name and not name.decode().startswith(VENDOR)
                   and (root / name.decode()).is_file()})


def folders(files: list[Path]) -> set[Path]:
    return {parent for path in files for parent in path.parents}


def dependency_errors(root: Path, files: list[Path]) -> list[str]:
    errors = []
    for path in files:
        area = path.parts[0]
        if path.suffix != '.py' or area not in PRODUCTION:
            continue
        try:
            tree = ast.parse((root / path).read_text(), filename=str(path))
        except SyntaxError as exc:
            errors.append(f'{path}:{exc.lineno}: {exc.msg}')
            continue
        for node in ast.walk(tree):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ''] if isinstance(node, ast.ImportFrom) and not node.level
                     else [])
            for name in names:
                top = name.split('.')[0]
                if top in {'research', 'tests'}:
                    errors.append(f'{path}:{node.lineno}: production imports {top}')
                if area == 'soundbox' and top in {'desktop', 'PySide6', 'PyQt6', 'PyQt5'}:
                    errors.append(f'{path}:{node.lineno}: Qt-free engine imports {top}')
                if area != 'soundbox_personal' and top == 'soundbox_personal':
                    errors.append(f'{path}:{node.lineno}: use provider admission for personal code')
    return errors


def _prose(text: str) -> str:
    # Code examples are not documentation links or heading targets.
    return re.sub(r'^(`{3,}|~{3,}).*?^\1[^\n]*$', '', text,
                  flags=re.MULTILINE | re.DOTALL)


def anchors(text: str) -> set[str]:
    result: set[str] = set()
    counts: dict[str, int] = {}
    for title in re.findall(r'^#{1,6}\s+(.+?)\s*#*$', _prose(text), re.MULTILINE):
        title = re.sub(r'<[^>]+>', '', title).lower()
        slug = ''.join(c for c in title if c.isalnum() or c in '_- ')
        slug = slug.replace(' ', '-')
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(f'{slug}-{count}' if count else slug)
    result.update(re.findall(r'<a\s+(?:name|id)=[\"\']([^\"\']+)', text))
    return result


def link_errors(root: Path, files: list[Path]) -> list[str]:
    errors = []
    headings: dict[Path, set[str]] = {}
    for path in files:
        if path.suffix != '.md':
            continue
        text = _prose((root / path).read_text())
        targets = re.findall(r'!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)', text)
        targets += re.findall(r'^\s{0,3}\[[^\]]+\]:\s*(<[^>]+>|\S+)', text, re.MULTILINE)
        for raw in targets:
            target = raw.strip('<>')
            url = urlsplit(target)
            if url.scheme or url.netloc or not target:
                continue
            resolved = ((root / path).parent / unquote(url.path)).resolve() if url.path else (root / path).resolve()
            # Absolute private evidence locations are not portable repo links.
            if not resolved.is_relative_to(root.resolve()):
                errors.append(f'{path}: link leaves checkout: {target}')
                continue
            if not resolved.exists():
                errors.append(f'{path}: missing link: {target}')
                continue
            if url.fragment and resolved.suffix == '.md':
                if resolved not in headings:
                    headings[resolved] = anchors(resolved.read_text())
                if unquote(url.fragment) not in headings[resolved]:
                    errors.append(f'{path}: missing heading: {target}')
    return errors


def check(root: Path) -> list[str]:
    files = authored_files(root)
    errors = []
    for folder in sorted(folders(files)):
        guide = root / folder / 'README.md'
        if not guide.is_file():
            errors.append(f'{folder}: missing README.md')
        elif len(guide.read_text().splitlines()) > GUIDE_LINES:
            errors.append(f'{folder}/README.md: keep the folder guide within {GUIDE_LINES} lines')
    if (root / 'AGENTS.md').read_bytes() != (root / 'CLAUDE.md').read_bytes():
        errors.append('AGENTS.md and CLAUDE.md differ')
    return errors + dependency_errors(root, files) + link_errors(root, files)


def main() -> int:
    errors = check(ROOT)
    for error in errors:
        print(error)
    if not errors:
        print('Folder guides, local Markdown links, instruction parity and static imports pass')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
