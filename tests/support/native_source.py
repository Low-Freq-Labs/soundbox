"""Read authored native sources for source contracts and numerical oracles."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def native_deck_source() -> str:
    """Expand the shader include while retaining the deck's own source text.

    Numerical tests mutate this combined text privately before compiling their
    oracle wrappers; the production header remains the only shader definition.
    """
    source = (ROOT / 'native/SoundboxDeck.mm').read_text()
    include = '#include "WaveformShader.h"'
    if source.count(include) != 1:
        raise ValueError('Native deck must include its waveform shader exactly once')
    shader = (ROOT / 'native/WaveformShader.h').read_text()
    return source.replace(include, shader, 1)


def waveform_shader(source: str) -> str:
    """Extract the one production Metal literal from a native source snapshot."""
    start, end = '@R"METAL(', ')METAL"'
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError('Expected one waveform shader literal')
    return source.split(start, 1)[1].split(end, 1)[0]
