"""Check the actual generic contracts and audit all their named proof dependencies."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from corpus import ROOT


MODULES = [
    "Mettapedia.GSLT.Parsing.SortedCanonicalSExprCodec",
    "Mettapedia.GSLT.LanguageDef.ModuleAlgebra.OriginView",
    "Mettapedia.GSLT.Parsing.NonemptySeparatorRefactoring",
    "Mettapedia.GSLT.Parsing.CanonicalProjectionSExprAdapter",
    "Mettapedia.GSLT.Parsing.SortedCodecMetadataAdmission",
    "Mettapedia.GSLT.LanguageDef.FreshNamedContext",
    "Mettapedia.GSLT.Parsing.SortedSchemaExtension",
]
PRESENTATION_MODULES = [
    "Mettapedia.GSLT.Parsing.ExactPresentation",
    "Mettapedia.GSLT.Parsing.CheckedTreePresentation",
    "Mettapedia.GSLT.Parsing.CheckedTreePresentationControls",
    "Mettapedia.GSLT.Parsing.SortedTreePresentationTemplates",
    "Mettapedia.GSLT.Parsing.PreparedSortedCodecSchema",
]
TYPE_MODULES = [
    "Mettapedia.Languages.C11.ArithmeticConversions",
    "Mettapedia.Languages.C11.TypeLanguage",
]
ALLOWED_AXIOMS = {"propext","Classical.choice","Quot.sound"}


def code_without_comments(text):
    result = []
    position,depth = 0,0
    while position<len(text):
        if text.startswith("/-",position):
            depth += 1; position += 2
        elif depth and text.startswith("-/",position):
            depth -= 1; position += 2
        elif not depth and text.startswith("--",position):
            end = text.find("\n",position)
            position = len(text) if end<0 else end
        else:
            character = text[position]
            result.append(character if not depth or character == "\n" else " ")
            position += 1
    if depth:
        raise RuntimeError("unterminated Lean comment")
    return "".join(result)


def named_theorems(code):
    namespace = re.search(r'^namespace ([^\s]+)',code,re.MULTILINE)
    if not namespace:
        raise RuntimeError("contract file lacks an explicit namespace")
    return [namespace[1]+"."+name for name in re.findall(r'\btheorem\s+([A-Za-z0-9_.?]+)',code)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lean-project",type=Path,default=ROOT.parent / "Mettapedia/lean/mettapedia")
    parser.add_argument("--lean-memory-mb",type=int)
    parser.add_argument("--output",type=Path,default=ROOT / "build/lean-contracts")
    args = parser.parse_args()
    project,output = args.lean_project.resolve(),args.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        parser.error("contract audit artifacts belong under build")
    output.mkdir(parents=True,exist_ok=True)
    lean = ["lake","env","lean","-DwarningAsError=true"]
    if args.lean_memory_mb is not None:
        lean += ["-M",str(args.lean_memory_mb)]
    files,theorems,imports = [],[],[]
    started = time.monotonic()
    groups = [(module,module+"Controls") for module in MODULES] + [PRESENTATION_MODULES, TYPE_MODULES]
    for group in groups:
        for name in group:
            source = project / (name.replace(".","/")+".lean")
            data = source.read_bytes()
            code = code_without_comments(data.decode())
            if re.search(r'\b(?:sorry|axiom|native_decide|theorem_wanted)\b|_wanted',code):
                raise RuntimeError("contract contains a proof gap or unchecked native decision: "+name)
            qualified = named_theorems(code)
            # Refresh exactly these supported module artifacts in dependency order.
            artifact = project / ".lake/build/lib/lean" / (name.replace(".","/")+".olean")
            artifact.parent.mkdir(parents=True,exist_ok=True)
            result = subprocess.run([*lean,"-o",str(artifact),str(source)],cwd=project,
                                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
            (output / (name.rsplit(".",1)[-1]+".log")).write_bytes(result.stdout)
            if result.returncode:
                raise RuntimeError("strict Lean contract check failed: "+name+": "+result.stdout.decode())
            if source.read_bytes() != data:
                raise RuntimeError("Lean contract changed during verification: "+name)
            files.append({"module":name,"source_sha256":hashlib.sha256(data).hexdigest(),"theorems":len(qualified)})
            theorems.extend(qualified); imports.append(name)
    audit = output / "Axioms.lean"
    audit.write_text("\n".join([*("import "+name for name in imports),
                               *("#print axioms "+name for name in theorems)])+"\n")
    result = subprocess.run([*lean,str(audit)],cwd=project,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
    (output / "axioms.log").write_bytes(result.stdout)
    if result.returncode:
        raise RuntimeError("named theorem audit failed: "+result.stdout.decode())
    log = result.stdout.decode()
    dependencies = {}
    for match in re.finditer(r"'([^']+)' depends on axioms:\s*\[(.*?)\]",log,re.DOTALL):
        axioms = {item.strip() for item in match[2].split(",") if item.strip()}
        if not axioms <= ALLOWED_AXIOMS:
            raise RuntimeError("unexpected proof axioms: "+str(axioms))
        dependencies[match[1]] = sorted(axioms)
    for name in re.findall(r"'([^']+)' does not depend on any axioms",log):
        dependencies[name] = []
    if set(theorems) != set(dependencies):
        raise RuntimeError("the named theorem audit did not account for every declaration")
    summary = {"modules":files,"named_theorems":len(theorems),"axioms":dependencies,
               "seconds":round(time.monotonic()-started,2),
               "boundary":"These generic codec, module-algebra, projection, admission, checked-presentation and finite binder-naming proofs, and the C11 arithmetic and type-compatibility models, do not prove the native C parser, scope hook, binder, elaborator, byte wire format, native compact adapter, macro expander or C compiler semantics."}
    (output / "summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(f"Lean contracts: {len(files)} strict module checks; {len(theorems)} named theorem axiom audits; {summary['seconds']}s")


if __name__ == "__main__":
    main()
