"""Dependency-sensitive input identities for source and provenance evidence."""

import shlex
from pathlib import Path

from corpus import run, sha
import qualification_cache as cache


def dependencies(data, cwd):
    text = data.decode().replace("\\\n", " ")
    target, separator, rest = text.partition(":")
    if not separator or target.strip() != "cesta-qualified":
        raise RuntimeError("unexpected compiler dependency rule")
    names = shlex.split(rest, comments=False, posix=True)
    return sorted({str((Path(cwd) / name.replace("$$", "$")).resolve()) for name in names})


def source_state(unit, work):
    """Both compilers' actual included inputs and line-marked preprocessing.

    Header bytes matter even when a comment change leaves the expanded tokens
    unchanged: it can move a spelling column used by provenance.
    """
    state = {}
    cwd = Path(unit["cwd"])
    for compiler in ("gcc", "clang"):
        command = ["clang"] if compiler == "clang" else unit["compiler"]
        depfile = (Path(work) / (compiler + "-inputs.d")).resolve()
        status, data = run(command + unit["flags"] +
                           ["-g0", "-E", "-MD", "-MF", str(depfile),
                            "-MT", "cesta-qualified", unit["source"]], cwd)
        if not status["accepted"]:
            raise RuntimeError(compiler + " source identity: " + status["diagnostic"][:500])
        files = {name: sha(Path(name).read_bytes()) for name in dependencies(depfile.read_bytes(), cwd)}
        if not files:
            raise RuntimeError("compiler supplied no source dependencies")
        state[compiler] = {"preprocessed_sha256": sha(data),
                           "dependencies_sha256": cache.digest(files), "dependencies": len(files)}
    return state
