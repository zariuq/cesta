"""Read and serialize data using HE's ordinary runner tokenizer."""

import json
from pathlib import Path
import sys

from hyperon import MeTTa, SExprParser
from hyperonpy import AtomKind


def serialize(atom):
    if atom.get_metatype() == AtomKind.SYMBOL:
        return atom.get_name()
    if atom.get_metatype() == AtomKind.EXPR:
        return "("+" ".join(serialize(child) for child in atom.get_children())+")"
    if atom.get_metatype() == AtomKind.GROUNDED:
        value = atom.get_object().value
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=True)
    raise ValueError("wire acquired a variable or a non-string grounded value")


runner = MeTTa()
parser = SExprParser(Path(sys.argv[1]).read_text())
atom = parser.parse(runner.tokenizer())
assert atom is not None and parser.parse(runner.tokenizer()) is None
print(serialize(atom))
