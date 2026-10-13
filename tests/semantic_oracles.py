"""Independent compiler oracles for Cesta's binding, types and conversions.

GCC and Clang each check a unit in their own preprocessing: GCC compiles the
type, layout and constant probes; Clang's AST checks binding, value categories
and the conversion chain of every expression, compiles its own probes, and its
IR checks that the conversions written as casts mean what the implicit ones
mean. A census classifies what the conversions do to values, and GCC's
-Wconversion family must report only classified conversions.
"""

import collections
import hashlib
import json
import re
import subprocess
from pathlib import Path

USE = re.compile(rb"^ \(use (\d+) (\d+) ([a-z-]+) (\d+|\(\))( folded)?\)$", re.M)
EXPR = re.compile(r'^ \(expr (\d+) (\d+) "((?:[^"\\]|\\.)*)" (lvalue|rvalue|designator)(.*)\)$')
CONVERSION = re.compile(r'\(([A-Za-z]+) "(?:[^"\\]|\\.)*"\)')
CAST = re.compile(r"\(cast ([A-Za-z]+)\)")
CENSUS = re.compile(rb"^ \(conversion (\d+) (\d+) ([a-z-]+) (\d+) (\d+)\)$", re.M)
COUNT = re.compile(rb"^ \(count (\w+) ([a-z-]+) (\d+)\)$", re.M)
WARNING = re.compile(r"^[^:]+:(\d+):(\d+): warning: (.*) \[-W(conversion|sign-conversion|float-conversion)\]$", re.M)
ARITHMETIC = re.compile(rb"\(arithmetic (\d+) (\d+)\)")
TRANSPARENT = {"ParenExpr", "ConstantExpr"}
TEMPORARY = re.compile(r"\bD\.\d+\b")
INTRINSIC_ALIGNMENT = re.compile(r"(call void @llvm\.mem(?:set|cpy|move)[^(]*\()(.*)$")
SOURCE_LOCATIONS = re.compile(r"^(!\d+ = !\{)i64 \d+(?:, i64 \d+)*(\})$")


def standard(unit):
    return "c11" if unit["corpus"] == "cetta" else "gnu11"


def cesta(binary, command, unit, path, *options, dialect="gcc"):
    result = subprocess.run([str(binary), command, "--std=" + standard(unit), "--dialect=" + dialect, *options, str(path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[:2000])
    return result.stdout, result.stderr.decode()


def clang_tree(path, unit):
    data = subprocess.run(["clang", *compile_flags(unit), "-fsyntax-only", "-std=" + standard(unit), "-Xclang", "-ast-dump=json", str(path)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    return json.loads(data)


def children(node):
    # an initializer list with an array filler lists its elements under "array_filler"
    return node.get("inner", []) + node.get("array_filler", [])


def binding(binary, unit, path, tree):
    """Every clang DeclRefExpr and goto agrees with the declaration Cesta resolves."""
    located, references, labels, implicit = {}, {}, {}, set()
    stack = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        location = node.get("loc", {})
        if "id" in node and "offset" in location:
            located[node["id"]] = location["offset"]
        if node.get("isImplicit") and "id" in node:
            implicit.add(node["id"])
        kind = node.get("kind")
        if kind == "LabelStmt":
            located[node["declId"]] = node["range"]["begin"]["offset"]
        elif kind == "DeclRefExpr":
            references[node["range"]["begin"]["offset"]] = node["referencedDecl"]["id"]
        elif kind == "GotoStmt":
            labels[node["range"]["begin"]["offset"]] = node["targetLabelDeclId"]
        stack.extend(children(node))
    data, _ = cesta(binary, "bind", unit, path, dialect="clang")
    uses = [(int(o), role.decode(), None if v == b"()" else int(v), bool(f)) for o, _, role, v, f in USE.findall(data)]
    ours = {o: v for o, role, v, f in uses if role == "reference"}
    folded = {o for o, role, v, f in uses if f}
    ours_labels = sorted((o, v) for o, role, v, f in uses if role == "label")
    result = collections.Counter()
    for offset, declaration in references.items():
        target = None if declaration in implicit else located.get(declaration)
        result["agree" if offset in ours and ours[offset] == target else "disagree"] += 1
    # clang folds array sizes and designators into types; those uses have no expression node
    result["extra"] = sum(1 for o, v in ours.items() if o not in references and v is not None and o not in folded)
    for offset, declaration in labels.items():
        following = [v for o, v in ours_labels if o > offset]
        result["labels agree" if following and following[0] == located.get(declaration) else "labels disagree"] += 1
    return result


RECORD = re.compile(r'^ \(record \d+ (struct|union) (\(\)|"(?:[^"\\]|\\.)*") (\d+):(\d+) (\d+) (\d+) (natural|special)((?: \([^()]*\))*)\)$')
MEMBER = re.compile(r"\((\S+) (\d+) (\d+) (\d+)(?: (\d+) (\d+))?\)")
LAYOUT_LINE = re.compile(r"^\s*(\d+)(?::(\d+)-(\d+)|:-)? \|( +)(.*?)\s*$")


def layouts(path, unit, types_data, *compiler_flags):
    """Every complete record's size, alignment and field positions, bit-fields
    included, agree with clang's record layout."""
    ours_named, ours_located = {}, {}
    for line in types_data.decode().splitlines():
        match = RECORD.match(line)
        if not match:
            continue
        kind, name, row, column, size, align, _, members = match.groups()
        fields = [(int(o), int(b) if b else None, int(w) if w else None) for _, o, _, _, b, w in MEMBER.findall(members)]
        record = (kind, int(size), int(align), fields)
        if name != "()":
            ours_named.setdefault((kind, json.loads(name)), []).append(record)
        ours_located[(kind, row + ":" + column)] = record
    dump = subprocess.run(["clang", *compile_flags(unit), *compiler_flags, "-fsyntax-only", "-std=" + standard(unit), "-Xclang", "-fdump-record-layouts-complete",
                           str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout.decode(errors="replace")
    result = collections.Counter()
    for block in dump.split("*** Dumping AST Record Layout")[1:]:
        lines = [line for line in block.splitlines() if line.strip()]
        header = LAYOUT_LINE.match(lines[0]).group(5)
        kind, _, name = header.partition(" ")
        if name in ("__NSConstantString_tag",):
            continue   # a clang-internal record
        size = int(re.search(r"sizeof=(\d+)", block).group(1))
        align = int(re.search(r"align=(\d+)", block).group(1))
        fields = []
        for line in lines[1:]:
            match = LAYOUT_LINE.match(line)
            if not match or len(match.group(4)) != 3:
                continue   # nested members and the summary line
            offset, first, last = match.group(1), match.group(2), match.group(3)
            zero_width = line.split("|")[0].strip().endswith(":-")
            fields.append((int(offset), 0 if zero_width else None if first is None else int(first),
                           0 if zero_width else None if first is None else int(last) - int(first) + 1))
        located = re.search(r"\((?:unnamed|anonymous) at [^)]*:(\d+):(\d+)\)$", name)
        candidates = [ours_located.get((kind, located.group(1) + ":" + located.group(2)))] if located else \
            ours_named.get((kind, name.split("::")[-1]), [])
        expected = (kind, size, align, fields)
        result["agree" if expected in candidates else "disagree" if any(candidates) else "unmatched"] += 1
    return result


def categories_and_conversions(binary, unit, path, tree, data=None):
    """Every expression's value category and implicit conversion chain agree with clang's."""
    nodes = {}
    stack = [(tree, [])]
    while stack:
        node, casts = stack.pop()
        if not isinstance(node, dict):
            continue
        kind = node.get("kind", "")
        if kind == "ImplicitCastExpr":
            stack.extend((child, casts + [node["castKind"]]) for child in children(node))
            continue
        if kind in TRANSPARENT:
            stack.extend((child, casts) for child in children(node))
            continue
        extent = node.get("range", {})
        begin, end = extent.get("begin", {}), extent.get("end", {})
        if "valueCategory" in node and "offset" in begin and "offset" in end:
            nodes.setdefault((begin["offset"], end["offset"] + end.get("tokLen", 0)), []).append(
                (kind, node["valueCategory"], list(reversed(casts)), node.get("castKind")))
        stack.extend((child, []) for child in children(node))
    if data is None:
        data, _ = cesta(binary, "types", unit, path, dialect="clang")
    result = collections.Counter()
    text = Path(path).read_bytes()
    for line in data.decode().splitlines():
        match = EXPR.match(line)
        if not match:
            continue
        begin, end, type_text, category, rest = match.groups()
        begin, end = int(begin), int(end)
        conversions = CONVERSION.findall(rest)
        cast = CAST.search(rest)
        candidates = nodes.get((begin, end))
        if not candidates:
            # an atomic builtin's name is part of clang's AtomicExpr, not a callee node
            source = text[begin:end]
            result["folded" if "(folded)" in rest else "atomic builtin name"
                   if source.startswith((b"__c11_atomic_", b"__atomic_")) else "unmatched"] += 1
            continue
        ours = "lvalue" if category == "lvalue" else "prvalue"   # clang: a C function designator is a prvalue

        def normalized(candidate):
            kind, clang_category, casts, clang_cast = candidate
            # clang models a member of a structure rvalue as an expiring temporary
            if clang_category == "xvalue":
                clang_category, casts = "prvalue", casts[1:] if casts[:1] == ["LValueToRValue"] else casts
            return clang_category, casts, clang_cast

        best = min((normalized(c) for c in candidates), key=lambda c: (c[0] != ours) + (c[1] != conversions))
        agrees = best[0] == ours and best[1] == conversions and (cast is None or best[2] is None or cast.group(1) == best[2])
        result["agree" if agrees else "disagree"] += 1
    return result


def compile_flags(unit):
    flags, skip = [], False
    for flag in unit.get("flags", []):
        if skip:
            skip = False
            continue
        if flag in ("-I", "-D", "-U", "-include", "-isystem", "-iquote", "-MF", "-MT", "-MQ", "-o"):
            skip = True
            continue
        if flag.startswith(("-I", "-D", "-U", "-M", "-W")) or flag == "-c":
            continue
        flags.append(flag)
    return flags + ["-w"]


def probes(binary, unit, path, work, compiler, dialect):
    """The probe rewrite compiles: every type, layout and constant Cesta claims."""
    data, report = cesta(binary, "probe", unit, path, dialect=dialect)
    probe = Path(work) / "probe.c"
    probe.write_bytes(data)
    result = subprocess.run([compiler, *compile_flags(unit), "-std=" + standard(unit), "-fsyntax-only", str(probe)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    probe.unlink()
    counts = {}
    layout = re.search(r"layout: (\d+) records, (\d+) member offsets, (\d+) enumeration constants \((\d+) records unnamed\)", report)
    expressions = re.search(r"probes: (\d+) typed expressions checked; (\d+) not rewritten \((\d+) unnamed types, (\d+) inside statement expressions\); (\d+) bit-fields", report)
    if layout:
        counts.update(zip(("records", "member offsets", "enumeration constants", "records unnamed"), map(int, layout.groups())))
    if expressions:
        counts.update(zip(("typed expressions", "not rewritten", "unnamed types", "inside statement expressions", "bit-fields by promotion"),
                          map(int, expressions.groups())))
    constants = re.search(r"; (\d+) integer constant values checked", report)
    if constants:
        counts["integer constant values"] = int(constants[1])
    return result.returncode == 0, counts, result.stderr.decode(errors="replace")[:1500]


def canonical_ir(text):
    locations = set(re.findall(r"!srcloc !(\d+)",text))
    lines = []
    for line in text.splitlines():
        # Only metadata actually referenced as inline-assembly source locations.
        identifier = re.match(r"!(\d+) =",line)
        if identifier and identifier[1] in locations:
            line = SOURCE_LOCATIONS.sub(r"\1source-locations\2", line)
        lines.append(line)
    return "\n".join(lines)


def compare_ir(implicit, explicit):
    """Permit only source locations and weaker memory-intrinsic alignment facts."""
    first, second = canonical_ir(implicit).splitlines(), canonical_ir(explicit).splitlines()
    differences = []
    equivalent = len(first) == len(second)
    for number, (a, b) in enumerate(zip(first, second), 1):
        if a == b:
            continue
        ma, mb = INTRINSIC_ALIGNMENT.search(a), INTRINSIC_ALIGNMENT.search(b)
        if not ma or not mb:
            equivalent = False
            continue
        pointer = re.compile(r"\bptr(?: align (\d+))? ")
        aligned_a, aligned_b = pointer.findall(ma[2]), pointer.findall(mb[2])
        strip_a, strip_b = pointer.sub("ptr ",a), pointer.sub("ptr ",b)
        weakening = (strip_a == strip_b and len(aligned_a) == len(aligned_b) and
                     all(int(x or 1) >= int(y or 1) for x,y in zip(aligned_a,aligned_b)))
        if not weakening:
            equivalent = False
        differences.append({"line": number, "only_weaker_alignment": weakening, "implicit": a, "explicit": b})
    return {"identical_ir": equivalent, "raw_identical_ir": implicit == explicit,
            "identical_except_source_locations": first == second,
            "alignment_differences": differences,
            "implicit_ir_sha256": hashlib.sha256(implicit.encode()).hexdigest(),
            "explicit_ir_sha256": hashlib.sha256(explicit.encode()).hexdigest()}


def explicit(binary, unit, path, work):
    """Written as casts, the conversions give clang the same IR; objects are compared too."""
    data, report = cesta(binary, "explicit", unit, path, dialect="clang")
    casts = int(report.split()[1])
    work = Path(work)
    ir, objects = [], []
    for name, source in (("implicit", Path(path).read_bytes()), ("explicit", data)):
        directory = work / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unit.i").write_bytes(source)
        flags = compile_flags(unit)
        ir.append(subprocess.run(["clang", *flags, "-O0", "-g0", "-S", "-emit-llvm", "unit.i", "-o", "-"],
                                 cwd=directory, stdout=subprocess.PIPE, check=True).stdout.decode())
        subprocess.run(["clang", *flags, "-g0", "-c", "unit.i", "-o", "unit.o"], cwd=directory, check=True)
        objects.append(hashlib.sha256((directory / "unit.o").read_bytes()).hexdigest())
    evidence = compare_ir(*ir)
    evidence.update({"identical_object": objects[0] == objects[1], "casts": casts,
                     "implicit_object_sha256": objects[0], "explicit_object_sha256": objects[1]})
    return evidence


def census(binary, unit, path):
    """What conversions do to values, and GCC's conversion warnings on them."""
    data, _ = cesta(binary, "census", unit, path)
    conversions = [tuple(map(int, (b, e, pb, pe))) for b, e, _, pb, pe in CENSUS.findall(data)]
    counts = collections.Counter({(k.decode(), c.decode()): int(n) for k, c, n in COUNT.findall(data)})
    flags = [flag for flag in compile_flags(unit) if flag != "-w"]
    warnings = subprocess.run(["gcc", *flags, "-std=" + standard(unit), "-fsyntax-only", "-Wconversion", "-Wsign-conversion",
                               "-Wfloat-conversion", "-fdiagnostics-column-unit=byte", "-fno-diagnostics-show-caret", str(path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stderr.decode(errors="replace")
    text = Path(path).read_bytes()
    starts = [0] + [i + 1 for i, byte in enumerate(text) if byte == 10]

    def opening(begin):
        while begin > 0 and text[begin - 1:begin] in (b"(", b" ", b"\t", b"\n"):
            begin -= 1
        return begin

    unmatched = []
    for line, column, message, _ in WARNING.findall(warnings):
        offset = starts[int(line) - 1] + int(column) - 1
        # GCC also warns about its own operand shortening, which names one type twice
        names = [aka or name for name, aka in re.findall(r"‘([^’]+)’(?: \{aka ‘([^’]+)’\})?", message)]
        if len(names) >= 2 and names[0] == names[1]:
            counts[("warning", "operand shortening")] += 1
            continue
        # the operand, the parentheses around it, or its expression when the compiler points at the operator
        if any(opening(b) <= offset < max(e, b + 1) or pb <= offset < pe for b, e, pb, pe in conversions):
            counts[("warning", "classified")] += 1
        else:
            counts[("warning", "unclassified")] += 1
            unmatched.append((int(line), message[:120]))
    return counts, unmatched


def arithmetic_steps_and_shapes(binary, unit, path):
    """The usual arithmetic conversions a unit applies, and its natural record shapes
    as (kind, size, alignment, ((offset, size, alignment)...)) for kernel admission."""
    data, _ = cesta(binary, "types", unit, path)
    shapes = set()
    for line in data.decode().splitlines():
        match = RECORD.match(line)
        if match and match.group(7) == "natural":
            members = tuple((int(o), int(s), int(a)) for _, o, s, a, _, _ in MEMBER.findall(match.group(8)))
            shapes.add((match.group(1), int(match.group(5)), int(match.group(6)), members))
    return {(int(a), int(b)) for a, b in ARITHMETIC.findall(data)}, shapes
