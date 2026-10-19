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
# The readable export: its lowering, its semantics, the correspondence proof,
# the evaluation-order discipline, completeness, the library laws and the
# counter-table profile, in dependency order.
EXPORT_MODULES = [
    "Mettapedia.GSLT.LanguageDef.MacroTemplateNames",
    "Mettapedia.GSLT.LanguageDef.MacroCheckedNarrowing",
    "Mettapedia.GSLT.LanguageDef.MacroExportPlan",
    "Mettapedia.GSLT.LanguageDef.MacroExportStateful",
    "Mettapedia.GSLT.LanguageDef.MacroExportCorrect",
    "Mettapedia.GSLT.LanguageDef.MacroExportTheorem",
    "Mettapedia.GSLT.LanguageDef.MacroExportOrder",
    "Mettapedia.GSLT.LanguageDef.MacroExportHeader",
    "Mettapedia.GSLT.LanguageDef.MacroExportComplete",
    "Mettapedia.GSLT.LanguageDef.MacroExportLibraries",
    "Mettapedia.GSLT.LanguageDef.ExportCounterTables",
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
    """Each public theorem with the namespace in force where it is declared."""
    if not re.search(r'^namespace ([^\s]+)',code,re.MULTILINE):
        raise RuntimeError("contract file lacks an explicit namespace")
    scopes, names = [], []
    for line in code.split("\n"):
        opened = re.match(r'namespace\s+([^\s]+)',line)
        if opened:
            scopes.append(("namespace",opened[1])); continue
        if re.match(r'(?:noncomputable\s+)?section\b',line) or re.match(r'mutual\b',line):
            scopes.append(("scope",None)); continue
        closed = re.match(r'end(?:\s+([^\s]+))?\s*$',line)
        if closed and scopes:
            scopes.pop(); continue
        declared = re.match(r'\s*(?:@\[[^\]]*\]\s*)?(?:protected\s+)?theorem\s+([A-Za-z0-9_.?\']+)',line)
        if declared:
            space = ".".join(name for kind,name in scopes if kind == "namespace")
            names.append(space+"."+declared[1] if space else declared[1])
    return names


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
    groups = [(module,module+"Controls") for module in MODULES] + [PRESENTATION_MODULES, TYPE_MODULES, EXPORT_MODULES]
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
               "boundary":"These generic codec, module-algebra, projection, admission, checked-presentation and finite binder-naming proofs, and the C11 arithmetic and type-compatibility models, do not prove the native C parser, scope hook, binder, elaborator, byte wire format, native compact adapter or C compiler semantics. The readable-export proofs relate each admitted macro rule's source use to the block Cesta's lowering produces, in an interpreter for the admitted C fragment; that the interpreter matches GCC/Clang on that fragment, and Cesta's printing of the block as C text, are checked by tests, not proved."}
    (output / "summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(f"Lean contracts: {len(files)} strict module checks; {len(theorems)} named theorem axiom audits; {summary['seconds']}s")


if __name__ == "__main__":
    main()
