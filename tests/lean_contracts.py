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
# Layouts: the field algebra, the codes and their laws, the declarations Cesta
# prints, field kinds, the C accessors and their meaning, and CeTTa's
# frame-identity layout as data.
LAYOUT_MODULES = [
    "Mettapedia.GSLT.LanguageDef.LayoutAlgebra",
    "Mettapedia.GSLT.LanguageDef.LayoutCodes",
    "Mettapedia.GSLT.LanguageDef.LayoutDeclarations",
    "Mettapedia.GSLT.LanguageDef.LayoutKinds",
    "Mettapedia.GSLT.LanguageDef.LayoutLowering",
    "Mettapedia.GSLT.LanguageDef.LayoutMigration",
    "Mettapedia.GSLT.LanguageDef.LayoutBitVec",
    "Mettapedia.GSLT.LanguageDef.LayoutViews",
    "Mettapedia.Languages.MeTTa.Bridges.GSLT.CeTTaFrameLayoutCode",
]
OPERATION_MODULES = [
    "Mettapedia.GSLT.LanguageDef.OperationContracts",
    "Mettapedia.GSLT.LanguageDef.OperationCostProtocol",
    "Mettapedia.GSLT.LanguageDef.OperationCostProtocolControls",
]
TABLE_MODULES = [
    "Mettapedia.GSLT.LanguageDef.DeclaredTables",
    "Mettapedia.GSLT.LanguageDef.DeclaredTablesControls",
    "Mettapedia.GSLT.LanguageDef.DeclaredCounterTables",
]
PROTOCOL_MODULES = [
    "Mettapedia.GSLT.LanguageDef.ExecutableProtocol",
    "Mettapedia.GSLT.LanguageDef.ExecutableProtocolSafety",
    "Mettapedia.GSLT.LanguageDef.ExecutableProtocolComposition",
    "Mettapedia.GSLT.LanguageDef.ExecutableProtocolControls",
    "Mettapedia.GSLT.LanguageDef.ExecutableProtocolInstrumentation",
    "Mettapedia.GSLT.LanguageDef.ExecutableProtocolInstrumentationCorrectness",
    "Mettapedia.GSLT.LanguageDef.ExecutableProtocolInstrumentationControls",
    "Mettapedia.GSLT.LanguageDef.ExecutableProtocolLoops",
    "Mettapedia.GSLT.LanguageDef.ExecutableProtocolLoopChecks",
]
CHECKED_MODULES = [
    "Mettapedia.GSLT.LanguageDef.CheckedBytes",
    "Mettapedia.GSLT.LanguageDef.CheckedBytePlans",
    "Mettapedia.GSLT.LanguageDef.CheckedByteSafety",
    "Mettapedia.GSLT.LanguageDef.CheckedByteModules",
    "Mettapedia.GSLT.LanguageDef.CheckedByteControls",
]
OWNERSHIP_MODULES = [
    "Mettapedia.Machines.CMemory.SpatialLoans",
    "Mettapedia.Machines.CMemory.Borrowing",
    "Mettapedia.Machines.CMemory.PermissionFlow",
    "Mettapedia.Languages.C11.BorrowingLayouts",
    "Mettapedia.GSLT.LanguageDef.OwnershipPlans",
    "Mettapedia.GSLT.LanguageDef.OwnershipCalls",
    "Mettapedia.GSLT.LanguageDef.OwnershipViews",
    "Mettapedia.GSLT.LanguageDef.OwnershipAdmission",
]
LAYOUT_BRIDGE_MODULES = [
    "Mettapedia.Languages.MeTTa.Bridges.GSLT.CeTTaFrameLayout",
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
    parser.add_argument("--layout-bridge",action="store_true",
                        help="also check the CeTTa runtime frame-codec correspondence and its axioms")
    parser.add_argument("--layouts-only",action="store_true",
                        help="check just the layout modules (and bridge when requested)")
    parser.add_argument("--operations-only",action="store_true",
                        help="check just the operation-contract modules")
    parser.add_argument("--tables-only",action="store_true",
                        help="check just the shared declared-table models")
    parser.add_argument("--protocols-only",action="store_true",
                        help="check executable protocol lowering, safety and composition")
    parser.add_argument("--checked-only",action="store_true",
                        help="check byte-buffer permissions, control-flow laws and examples")
    parser.add_argument("--ownership-only",action="store_true",
                        help="check allocation-range ownership, permission flow and C layout bridges")
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
    groups = [(module,module+"Controls") for module in MODULES] + [PRESENTATION_MODULES, TYPE_MODULES, EXPORT_MODULES, LAYOUT_MODULES,
                                                                  OPERATION_MODULES, TABLE_MODULES, PROTOCOL_MODULES, CHECKED_MODULES, OWNERSHIP_MODULES]
    if args.layouts_only:
        groups = [LAYOUT_MODULES]
    if args.operations_only:
        groups = [OPERATION_MODULES]
    if args.tables_only:
        groups = [TABLE_MODULES]
    if args.protocols_only:
        groups = [PROTOCOL_MODULES]
    if args.checked_only:
        groups = [CHECKED_MODULES]
    if args.ownership_only:
        groups = [OWNERSHIP_MODULES]
    if args.layout_bridge:
        groups.append(LAYOUT_BRIDGE_MODULES)
    for group in groups:
        for name in group:
            source = project / (name.replace(".","/")+".lean")
            data = source.read_bytes()
            code = code_without_comments(data.decode())
            if re.search(r'\b(?:sorry|axiom|native_decide|bv_decide|theorem_wanted)\b|_wanted',code):
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
               "layout_bridge_checked":args.layout_bridge,
               "layouts_only":args.layouts_only,
               "seconds":round(time.monotonic()-started,2),
               "boundary":"These generic codec, module-algebra, projection, admission, checked-presentation and finite binder-naming proofs, and the C11 arithmetic and type-compatibility models, do not prove the native C parser, scope hook, binder, elaborator, byte wire format, native compact adapter or C compiler semantics. The readable-export proofs relate each admitted macro rule's source use to the block Cesta's lowering produces, in an interpreter for the admitted C fragment; that the interpreter matches GCC/Clang on that fragment, and Cesta's printing of the block as C text, are checked by tests, not proved. Layout proofs cover the word algebra and recorded unsigned-expression lowering. Kernel checks of location metadata establish declaration conditions, not execution under the C11 memory model. Layout printing, compilation and generated atomic operations remain tested boundaries."}
    (output / "summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(f"Lean contracts: {len(files)} strict module checks; {len(theorems)} named theorem axiom audits; {summary['seconds']}s")


if __name__ == "__main__":
    main()
