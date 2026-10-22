"""Kernel-check actual typed bundles, ordered export views and counter embeddings.

The native normalized Atom records are retained and decoded independently in
Lean. These finite checks do not establish native byte-reader or C-emission
adequacy. Text in this bridge must decode as UTF-8 without replacement.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from export_admission import atom
from export_counters import counter_code
from lean_contracts import ALLOWED_AXIOMS, code_without_comments, named_theorems
from metadata_admission import lean_string
from oracles.pro_compact_cesta import Sym, Text, dumps, parse

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "build/cesta"
DEFAULT_LEAN = ROOT.parent / "Mettapedia/lean/mettapedia"
MODULES = ["DeclaredTables", "DeclaredCounterTables", "DeclaredTablesControls"]
SPACE = "Mettapedia.GSLT.LanguageDef."


def sha(data):
    return hashlib.sha256(data).hexdigest()


def headed(value, name):
    return isinstance(value, list) and bool(value) and value[0] == Sym(name)


def sym(value):
    if not isinstance(value, Sym):
        raise ValueError("expected a symbol in a normalized table record")
    return str(value)


def fields(value):
    return {sym(part[0]): part for part in value if isinstance(part, list) and part}


def lean_list(values):
    return "[" + ", ".join(values) + "]"


def strings(values):
    return lean_list(lean_string(sym(value)) for value in values)


def scalar_type(value):
    if isinstance(value, Sym):
        return {"Nat": ".nat", "Identifier": ".identifier", "Text": ".text", "Bool": ".boolean"}[str(value)]
    if headed(value, "Enum"):
        return "(.enumeration " + strings(value[1:]) + ")"
    raise ValueError("unsupported scalar type in normalized native record")


def column_type(value):
    if headed(value, "Ref") and len(value) == 3:
        return "(.reference " + lean_string(sym(value[1])) + " " + lean_string(sym(value[2])) + ")"
    return "(.scalar " + scalar_type(value) + ")"


def schema_tables(bundle):
    if not headed(bundle, "cesta-tables/1"):
        raise ValueError("expected a cesta-tables/1 bundle")
    tables = {}
    for table in bundle[1:]:
        parts = fields(table[2:])
        tables[sym(table[1])] = {"atom": table, "columns": parts["Columns"][1:], "parts": parts}
    return tables


def resolve_scalar(type_atom, tables):
    if not headed(type_atom, "Ref"):
        return type_atom
    target = tables[sym(type_atom[1])]
    column = next(c for c in target["columns"] if c[1] == type_atom[2])
    if headed(column[2], "Ref"):
        raise ValueError("reference target itself has reference type")
    return column[2]


def cell(value, type_atom, tables):
    type_atom = resolve_scalar(type_atom, tables)
    if type_atom == Sym("Nat"):
        return "(.nat " + str(int(sym(value), 10)) + ")"
    if type_atom == Sym("Text"):
        if not isinstance(value, Text):
            raise ValueError("expected Text in native normalized row")
        return "(.text " + lean_string(value.data.decode("utf-8", errors="strict")) + ")"
    if type_atom == Sym("Bool"):
        value = sym(value)
        if value not in ("true", "false"):
            raise ValueError("expected true/false in native normalized Bool row")
        return "(.boolean " + value + ")"
    if type_atom == Sym("Identifier") or headed(type_atom, "Enum"):
        return "(.identifier " + lean_string(sym(value)) + ")"
    raise ValueError("unsupported normalized row scalar")


def row(value, columns, tables):
    if not headed(value, "Row") or len(value) != len(columns) + 1:
        raise ValueError("normalized row has the wrong shape")
    return lean_list(cell(v, c[2], tables) for v, c in zip(value[1:], columns))


def bundle_data(bundle):
    tables = schema_tables(bundle)
    result = []
    for original in bundle[1:]:
        t = tables[sym(original[1])]
        columns = lean_list("⟨" + lean_string(sym(c[1])) + ", " + column_type(c[2]) + "⟩" for c in t["columns"])
        unique = lean_list(strings(p[1:]) for p in original[2:] if headed(p, "Unique"))
        dense = t["parts"].get("Dense")
        dense = "none" if dense is None else "(some " + lean_string(sym(dense[1])) + ")"
        rows = lean_list(row(r, t["columns"], tables) for r in t["parts"]["Rows"][1:])
        result.append("⟨" + lean_string(sym(original[1])) + ", " + columns + ", " +
                      strings(t["parts"]["Key"][1:]) + ", " + unique + ", " + dense + ", " + rows + "⟩")
    return lean_list(result)


def mutations(bundle):
    """Corrupt the actual source record, independently of the native checker."""
    def changed(label, mutate):
        value = copy.deepcopy(bundle)
        mutate(value)
        return label, value

    if len(bundle) <= 1:
        return []
    out = [changed("duplicate-table", lambda b: b.append(copy.deepcopy(b[1]))),
           changed("empty-columns", lambda b: b[1].__setitem__(2, [Sym("Columns")]))]
    key_index = next(i for i, p in enumerate(bundle[1]) if headed(p, "Key"))
    out += [changed("empty-primary", lambda b: b[1].__setitem__(key_index, [Sym("Key")])),
            changed("absent-primary", lambda b: b[1].__setitem__(key_index, [Sym("Key"), Sym("cesta_absent_column")])),
            changed("repeated-key-column", lambda b: b[1][key_index].append(b[1][key_index][1])),
            changed("duplicate-column", lambda b: b[1][2].append(copy.deepcopy(b[1][2][1])))]
    for table_index, table in enumerate(bundle[1:], 1):
        parts = fields(table[2:])
        row_index = next(i for i, p in enumerate(table) if headed(p, "Rows"))
        if len(parts["Rows"]) > 1:
            out += [changed("short-row", lambda b, i=table_index, j=row_index: b[i][j][1].pop()),
                    changed("long-row", lambda b, i=table_index, j=row_index: b[i][j][1].append(Sym("0"))),
                    changed("repeated-primary-row", lambda b, i=table_index, j=row_index: b[i][j].append(copy.deepcopy(b[i][j][1])))]
            scalar = resolve_scalar(parts["Columns"][1][2], schema_tables(bundle))
            bad = Sym("wrong_text") if scalar == Sym("Text") else Text(b"wrong_scalar")
            out.append(changed("wrong-cell-sort", lambda b, i=table_index, j=row_index, bad=bad: b[i][j][1].__setitem__(1,bad)))
            break
    for ti, table in enumerate(bundle[1:], 1):
        parts = fields(table[2:])
        rows_index = next(i for i, p in enumerate(table) if headed(p, "Rows"))
        for ci, column in enumerate(parts["Columns"][1:], 1):
            if column[2] == Sym("Nat") and len(parts["Rows"]) > 1:
                out.append(changed("uint64-overflow", lambda b, i=ti, j=rows_index, c=ci:
                                   b[i][j][1].__setitem__(c,Sym("18446744073709551616"))))
                out.append(changed("noncanonical-natural", lambda b, i=ti, j=rows_index, c=ci:
                                   b[i][j][1].__setitem__(c,Sym("00"))))
            if headed(column[2], "Ref"):
                out.append(changed("absent-reference-table", lambda b, i=ti, c=ci:
                                   b[i][2][c][2].__setitem__(1,Sym("cesta_absent_table"))))
            if headed(column[2], "Enum"):
                out.append(changed("empty-enum", lambda b, i=ti, c=ci:
                                   b[i][2][c].__setitem__(2,[Sym("Enum")])) )
    seen = set()
    return [(label, value) for label,value in out if not (label in seen or seen.add(label))]


def lean_command(project, memory):
    command = ["lake", "env", "lean", "-DwarningAsError=true"]
    if memory is not None:
        command += ["-M", str(memory)]
    return command


class Gate:
    def __init__(self, args):
        self.args = args
        self.work = args.output.resolve()
        self.project = args.lean_project.resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.lines = ["import " + SPACE + name for name in MODULES]
        self.lines += ["open Mettapedia.GSLT.LanguageDef",
                       "open Mettapedia.GSLT.LanguageDef.MacroExportPlan (Atom)",
                       "open Mettapedia.GSLT.LanguageDef.DeclaredTables",
                       "set_option Elab.async false", "set_option maxRecDepth 100000",
                       "set_option synthInstance.maxSize 1024", "namespace CestaTableAdmission"]
        self.theorems = []
        self.records = []
        self.bundles = {}
        self.module_records = []
        self.inputs = []

    def theorem(self, name, statement, proof="by decide +kernel"):
        self.lines.append(f"theorem {name} : {statement} := {proof}")
        self.theorems.append("CestaTableAdmission."+name)

    def run_native(self, args, ok=True):
        result = subprocess.run([str(BINARY), *map(str,args)],cwd=ROOT,capture_output=True)
        if (result.returncode==0) != ok:
            raise RuntimeError("native command unexpected status: "+str(args)+"\n"+
                               result.stderr.decode("utf-8",errors="replace"))
        return result.stdout

    def refresh_modules(self):
        for name in MODULES:
            module = SPACE+name
            source = self.project / (module.replace(".","/")+".lean")
            data = source.read_bytes()
            code = code_without_comments(data.decode())
            if re.search(r"\b(sorry|admit|axiom|native_decide|bv_decide|theorem_wanted)\b|_wanted",code):
                raise RuntimeError("unsupported proof dependency in "+module)
            target = self.project / ".lake/build/lib/lean" / (module.replace(".","/")+".olean")
            target.parent.mkdir(parents=True,exist_ok=True)
            result = subprocess.run([*lean_command(self.project,self.args.lean_memory_mb),
                                     "-o",str(target),str(source)],cwd=self.project,capture_output=True)
            (self.work / (name+".log")).write_bytes(result.stdout+result.stderr)
            if result.returncode or source.read_bytes()!=data:
                raise RuntimeError("strict generic module check failed/changed: "+module+"\n"+
                                   (result.stdout+result.stderr).decode(errors="replace"))
            names = named_theorems(code)
            self.theorems.extend(names)
            self.module_records.append({"module":module,"sha256":sha(data),"theorems":len(names)})

    def add_bundle(self, bundle, origin, mutants=True):
        signature = dumps(bundle)
        if signature in self.bundles:
            return self.bundles[signature]
        n = len(self.bundles)
        name = "bundle"+str(n)
        self.bundles[signature] = name
        self.lines += [f"def {name}Source : Atom := {atom(bundle)}",
                       f"def {name} : Bundle := {bundle_data(bundle)}"]
        self.theorem(name+"Decoded",f"decodeBundle {name}Source = some {name}")
        self.theorem(name+"Checked",f"check {name} = true")
        self.theorem(name+"Valid",f"BundleValid {name}",f"(check_iff _).mp {name}Checked")
        self.theorem(name+"SourceAdmitted",f"checkSource {name}Source = true",
                     f"by simpa only [checkSource, {name}Decoded] using {name}Checked")
        refusals = []
        if mutants:
            for label,bad in mutations(bundle):
                path = self.work / (name+"-"+label+".data")
                path.write_text(dumps(bad)+"\n")
                self.run_native(["tables","check",path],False)
                mutant = name+"Bad"+str(len(refusals))
                self.lines.append(f"def {mutant} : Atom := {atom(bad)}")
                self.theorem(mutant+"Refused",f"checkSource {mutant} = false")
                refusals.append(label)
        self.records.append({"kind":"bundle","name":name,"origin":origin,
                             "tables":len(bundle)-1,"rows":sum(len(fields(t[2:])["Rows"])-1 for t in bundle[1:]),
                             "source_sha256":sha(signature.encode()),"mutants":refusals})
        return name

    def add_view(self, record, bundle, bundle_name, origin):
        n = sum(r["kind"]=="view" for r in self.records)
        name = "view"+str(n)
        table_name = sym(record[1])
        tables = schema_tables(bundle)
        target = tables[table_name]
        table_index = next(i for i,t in enumerate(bundle[1:]) if sym(t[1])==table_name)
        parts = fields(record[2:])
        columns = [next(c for c in target["columns"] if c[1]==col) for col in parts["Columns"][1:]]
        predicates = []
        for p in parts["Where"][1:]:
            field = next(c for c in target["columns"] if c[1]==p[0])
            predicates.append("("+lean_string(sym(p[0]))+", "+cell(p[1],field[2],tables)+")")
        self.lines += [f"def {name}Table : Table := {bundle_name}[{table_index}]",
                       f"def {name}Predicates : List (String × Cell) := {lean_list(predicates)}",
                       f"def {name}Columns : List String := {strings(parts['Columns'][1:])}",
                       f"def {name}Actual : List Row := {lean_list(row(r,columns,tables) for r in parts['Rows'][1:])}"]
        self.theorem(name+"TableLookup",f"findTable {bundle_name} {lean_string(table_name)} = some {name}Table")
        self.theorem(name+"Checked",f"checkViewPlan {bundle_name} {name}Table {name}Predicates {name}Columns "
                     f"(select {name}Table {name}Predicates) {name}Actual = true")
        self.theorem(name+"Correspondence",
                     f"ViewNamesValid {bundle_name} {name}Table {name}Predicates {name}Columns ∧ "
                     f"List.Sublist (select {name}Table {name}Predicates) {name}Table.rows ∧ "
                     f"(∀ source, source ∈ select {name}Table {name}Predicates ↔ source ∈ {name}Table.rows ∧ "
                     f"∀ predicate ∈ {name}Predicates, cellAt {name}Table source predicate.1 = some predicate.2) ∧ "
                     f"List.Forall₂ (fun source target => project {name}Table {name}Columns source = some target) "
                     f"(select {name}Table {name}Predicates) {name}Actual",
                     f"checkViewPlan_sound {name}Checked")
        self.theorem(name+"AbsentColumnRefused",f'checkViewPlan {bundle_name} {name}Table '
                     f'{name}Predicates ["cesta_absent_column"] [] [] = false')
        self.theorem(name+"WrongPredicateRefused",f'checkViewNames {bundle_name} {name}Table '
                     f'[("cesta_absent_column", .nat 0)] {name}Columns = false')
        if len(parts["Rows"])>1:
            self.theorem(name+"MissingOutputRefused",f"checkViewPlan {bundle_name} {name}Table {name}Predicates "
                         f"{name}Columns (select {name}Table {name}Predicates) [] = false")
        if len(parts["Rows"])>2:
            self.theorem(name+"ReorderedOutputRefused",f"checkViewPlan {bundle_name} {name}Table {name}Predicates "
                         f"{name}Columns (select {name}Table {name}Predicates) {name}Actual.reverse = false")
        self.records.append({"kind":"view","name":name,"origin":origin,"table":table_name,
                             "rows":len(parts["Rows"])-1,"columns":[sym(c) for c in parts["Columns"][1:]],
                             "predicate_count":len(predicates)})

    def bundle_file(self, path):
        data = path.read_bytes()
        original, = parse(data)
        normalized = self.run_native(["tables","check",path])
        checked, = parse(normalized)
        if checked != original:
            raise RuntimeError("native normalization changed the source table data")
        (self.work / ("input"+str(len(self.inputs))+".data")).write_bytes(normalized)
        self.inputs.append({"kind":"bundle","path":str(path),"sha256":sha(data),"native_sha256":sha(normalized)})
        self.add_bundle(checked,str(path))

    def plan_file(self, path):
        data = path.read_bytes()
        self.inputs.append({"kind":"plan","path":str(path),"sha256":sha(data)})
        previous = None
        for index, record in enumerate(parse(data)):
            if headed(record,"cesta-tables/1"):
                previous = (record,self.add_bundle(record,str(path)+":"+str(index)))
            elif headed(record,"cesta-table-view/1"):
                if previous is None:
                    raise ValueError("table view has no preceding bundle record")
                self.add_view(record,*previous,str(path)+":"+str(index))
            else:
                raise ValueError("unsupported record in table-only native export plan")

    def legacy_counter(self, path):
        """Reuse the proved dense-counter checker for the actual legacy rows."""
        data = path.read_bytes()
        source, = parse(data)
        if not headed(source,"cesta-counters/1") or len(source)!=7:
            raise ValueError("legacy counter requires the original cesta-counters/1 record")
        parts = fields(source[1:6])
        rows = [(int(sym(r[0]),10),sym(r[1]),r[2].data.decode("utf-8",errors="strict")) for r in source[6][1:]]
        if not rows:
            raise ValueError("the native legacy adapter requires a nonempty counter table")
        n = sum(r["kind"]=="counter" for r in self.records)
        name = "legacy"+str(n)
        enum,prefix,count,names = (sym(parts[k][1]) for k in ["enum","prefix","count","names"])
        spec = self.work / (name+".export")
        spec.write_text('(cesta-export/1 (header CESTA_COUNTER_ADMISSION_'+str(n)+'_H) '
                        '(counters '+json.dumps(str(path))+'))\n')
        plan = self.work / (name+".plan")
        self.run_native(["export-header","--plan",plan,spec,"-o",self.work/(name+".h")])
        printed, = parse(plan.read_bytes())
        signature = dumps(printed)
        bundle_name = name+"Bundle"
        if signature in self.bundles:
            raise RuntimeError("a legacy counter embedding was already admitted through a different input; "
                               "supply legacy inputs before their generic plan")
        self.bundles[signature] = bundle_name
        self.inputs.append({"kind":"legacy-counter","path":str(path),"sha256":sha(data),
                            "generated_plan_sha256":sha(plan.read_bytes())})
        chunks = [rows[i:i+self.args.counter_chunk] for i in range(0,len(rows),self.args.counter_chunk)]
        row_names,name_codes,spelling_codes,proofs = [],[],[],[]
        for j,part in enumerate(chunks):
            r,nc,sc = name+"Rows"+str(j),name+"Names"+str(j),name+"Spellings"+str(j)
            row_names.append(r); name_codes.append(nc); spelling_codes.append(sc)
            self.lines += [f"def {r} : List ExportCounterTables.Row := "+
                           lean_list("⟨"+str(i)+", "+lean_string(label)+", "+lean_string(spelling)+"⟩"
                                     for i,label,spelling in part),
                           f"def {nc} : List Nat := "+lean_list(str(counter_code(label)) for _,label,_ in part),
                           f"def {sc} : List Nat := "+lean_list(str(counter_code(spelling)) for _,_,spelling in part)]
            proof = name+"Chunk"+str(j)
            self.theorem(proof,f"ExportCounterTables.rowsOk {r} {nc} {sc} = true")
            proofs.append(proof)

        def appended(parts):
            return " ++ (".join(parts)+")"*(len(parts)-1)

        all_rows,all_names,all_spellings = map(appended,[row_names,name_codes,spelling_codes])
        self.lines += [f"def {name} : ExportCounterTables.Table := "
                       f"⟨{lean_string(enum)}, {lean_string(prefix)}, {lean_string(count)}, {lean_string(names)}, {all_rows}⟩",
                       f"def {name}Names : List Nat := {all_names}",
                       f"def {name}Spellings : List Nat := {all_spellings}",
                       f"def {name}Source : Atom := {atom(source)}"]
        self.theorem(name+"Decoded",f"ExportCounterTables.decodeTable {name}Source = some {name}","by rfl")
        self.theorem(name+"NameCodesDistinct",
                     f"ExportCounterTables.allDistinct ({counter_code(count)} :: {name}Names) .leaf = true")
        self.theorem(name+"SpellingCodesDistinct",
                     f"ExportCounterTables.allDistinct {name}Spellings .leaf = true")
        self.theorem(name+"TableChecks",f"ExportCounterTables.tableOk {name} {counter_code(count)} = true")
        combined = proofs[-1]
        for proof in reversed(proofs[:-1]):
            combined = f"ExportCounterTables.rowsOk_append {proof} ({combined})"
        self.theorem(name+"Dense",f"ExportCounterTables.Dense {name}",
                     f"ExportCounterTables.dense_of_checked {name} {name}Names {name}Spellings {counter_code(count)} "
                     f"({combined}) {name}NameCodesDistinct {name}SpellingCodesDistinct {name}TableChecks")
        self.lines += [f"def {bundle_name} : Bundle := [DeclaredCounterTables.table {name}]",
                       f"def {bundle_name}Source : Atom := {atom(printed)}"]
        self.theorem(bundle_name+"Decoded",f"decodeBundle {bundle_name}Source = some {bundle_name}")
        self.theorem(bundle_name+"Checked",f"check {bundle_name} = true",
                     f"DeclaredCounterTables.check_of_dense {name}Dense (by decide +kernel)")
        self.theorem(bundle_name+"Valid",f"BundleValid {bundle_name}",f"(check_iff _).mp {bundle_name}Checked")
        self.theorem(bundle_name+"SourceAdmitted",f"checkSource {bundle_name}Source = true",
                     f"by simpa only [checkSource, {bundle_name}Decoded] using {bundle_name}Checked")

        # A changed recorded ID is refused from its mandatory Dense component;
        # the negative does not evaluate quadratic string distinctness again.
        bad = copy.deepcopy(printed)
        bad_rows = fields(bad[1][2:])["Rows"]
        bad_rows[1][1] = Sym("1")
        bad_name = name+"ChangedId"
        self.lines += [f"def {bad_name}Source : Atom := {atom(bad)}",
                       f"def {bad_name} : Bundle := {bundle_data(bad)}"]
        self.theorem(bad_name+"Decoded",f"decodeBundle {bad_name}Source = some {bad_name}")
        self.theorem(bad_name+"DenseRefused",f"checkDense {bad_name}[0] = false")
        self.theorem(bad_name+"Refused",f"check {bad_name} = false",
                     f"check_false_of_dense_failure (List.getElem_mem (by decide)) {bad_name}DenseRefused")
        self.theorem(bad_name+"SourceRefused",f"checkSource {bad_name}Source = false",
                     f"by simpa only [checkSource, {bad_name}Decoded] using {bad_name}Refused")
        self.records.append({"kind":"counter","name":name,"rows":len(rows),"chunks":len(chunks),
                             "original_source":str(path),"native_plan":str(plan),
                             "mutants":["changed-supplied-ID"],
                             "admission":"existing chunked Dense evidence -> generic schema; exact native normalized embedding decoded"})

    def finish(self, started, binary_hash):
        self.lines += ["end CestaTableAdmission"]
        self.lines += ["#print axioms "+name for name in self.theorems]
        source = self.work / "ActualTables.lean"
        source.write_text("\n".join(self.lines)+"\n")
        result = subprocess.run([*lean_command(self.project,self.args.lean_memory_mb),str(source)],
                                cwd=self.project,capture_output=True)
        (self.work / "actual-tables.log").write_bytes(result.stdout+result.stderr)
        output = (result.stdout+result.stderr).decode(errors="replace")
        if result.returncode:
            raise RuntimeError("actual table admission failed:\n"+output[-14000:])
        dependencies = {}
        for match in re.finditer(r"'([^']+)' depends on axioms:\s*\[(.*?)\]",output,re.DOTALL):
            found = {v.strip() for v in match[2].split(",") if v.strip()}
            if not found <= ALLOWED_AXIOMS:
                raise RuntimeError("unexpected proof axiom: "+str(found))
            dependencies[match[1]] = sorted(found)
        for name in re.findall(r"'([^']+)' does not depend on any axioms",output):
            dependencies[name] = []
        if set(dependencies)!=set(self.theorems):
            raise RuntimeError("named theorem audit does not cover the exact generated contract")
        if sha(BINARY.read_bytes()) != binary_hash:
            raise RuntimeError("native binary changed during admission; refresh the recorded evidence")
        summary = {"inputs":self.inputs,"records":self.records,"modules":self.module_records,
                   "native_binary_sha256":binary_hash,"generated_lean_sha256":sha(source.read_bytes()),
                   "lean_toolchain":(self.project/"lean-toolchain").read_text().strip(),
                   "named_theorems":len(self.theorems),"axioms":dependencies,
                   "seconds":round(time.monotonic()-started,2),
                   "boundary":"Finite normalized Atom data, schema admission, preserved IDs and ordered projections are kernel checked. Native byte parsing, printer/C compiler adequacy and operation/counter semantic laws are separate. Text requires lossless UTF-8 decoding."}
        (self.work / "summary.json").write_text(json.dumps(summary,indent=2)+"\n")
        print(json.dumps({"bundles":len(self.bundles),
                          "views":sum(r["kind"]=="view" for r in self.records),
                          "counter_rows":sum(r["rows"] for r in self.records if r["kind"]=="counter"),
                          "theorems":len(self.theorems),"seconds":summary["seconds"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle",type=Path,action="append",default=[])
    parser.add_argument("--plan",type=Path,action="append",default=[])
    parser.add_argument("--legacy-counter",type=Path,action="append",default=[])
    parser.add_argument("--counter-chunk",type=int,default=100)
    parser.add_argument("--lean-project",type=Path,default=DEFAULT_LEAN)
    parser.add_argument("--lean-memory-mb",type=int)
    parser.add_argument("--output",type=Path,default=ROOT/"build/table-admission")
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT/"build"):
        parser.error("admission artifacts belong under build")
    if args.counter_chunk<=0:
        parser.error("counter chunks must be nonempty")
    gate = Gate(args)
    started = time.monotonic()
    binary_hash = sha(BINARY.read_bytes())
    gate.refresh_modules()
    if not args.bundle and not args.plan and not args.legacy_counter:
        args.bundle = [ROOT/"tests/tables/Registry.data"]
        args.legacy_counter = [ROOT/"tests/export/counters.data"]
        normalized = gate.run_native(["tables","check",args.bundle[0]])
        (gate.work/"registry.data").write_bytes(normalized)
        spec = gate.work/"registry.export"
        spec.write_text('(cesta-export/1 (header ADMISSION_REGISTRY_H) (profile c11) '
                        '(table-enum "registry.data" Profiles (enum ProfileId) (prefix PROFILE_) (id id) (name code)) '
                        '(table-rows "registry.data" Profiles (macro ENABLED_PROFILES) '
                        '(columns id code label) (where enabled true)))\n')
        plan = gate.work/"registry.plan"
        gate.run_native(["export-header","--plan",plan,spec,"-o",gate.work/"registry.h"])
        args.plan = [plan]
    for path in args.legacy_counter:
        gate.legacy_counter(path.resolve())
    for path in args.bundle:
        gate.bundle_file(path.resolve())
    for path in args.plan:
        gate.plan_file(path.resolve())
    gate.finish(started,binary_hash)


if __name__ == "__main__":
    main()
