#!/usr/bin/env python3
"""Independent N1570 source-grammar coverage oracle.

The reference data is extracted from clause 6, cross-checked with Annex A, and
keeps the displayed productions and citations. Ordered RHS normalization and
regular-language comparisons inspect actual authored source; labels serve only
as diagnostics. This oracle does not prove the native parser implementation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).resolve().parent / "data"
ISO_MODULES = ("CLex", "CExpr", "CDecl", "CStmt", "CExternal")


class CoverageError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CoverageError(message)


@dataclass(frozen=True)
class Meta:
    kind: str
    text: str
    line: int


def meta_tokens(source):
    """Byte-aware adapter scanner, independent of the native scanner."""
    pos, line, out = 0, 1, []
    escapes = {"n": 10, "r": 13, "t": 9, "b": 8, "f": 12,
               '"': 34, "'": 39, "\\": 92}
    while pos < len(source):
        if source[pos].isspace():
            line += source[pos] == "\n"
            pos += 1
            continue
        if source.startswith("--", pos):
            end = source.find("\n", pos)
            pos = len(source) if end < 0 else end
            continue
        if source.startswith("{-", pos):
            end = source.find("-}", pos + 2)
            require(end >= 0, f"line {line}: unterminated source comment")
            line += source[pos:end+2].count("\n")
            pos = end + 2
            continue
        begin_line = line
        char = source[pos]
        if char in "\"'":
            quote, value = char, bytearray()
            pos += 1
            while pos < len(source) and source[pos] != quote:
                char = source[pos]
                pos += 1
                if char == "\\":
                    require(pos < len(source), f"line {line}: unterminated escape")
                    escaped = source[pos]
                    pos += 1
                    if escaped == "x":
                        digits = source[pos:pos+2]
                        require(len(digits) == 2 and re.fullmatch(r"[0-9a-fA-F]{2}", digits),
                                f"line {line}: expected a two-digit grammar byte escape")
                        value.append(int(digits, 16))
                        pos += 2
                    else:
                        require(escaped in escapes, f"line {line}: unknown grammar escape {escaped!r}")
                        value.append(escapes[escaped])
                else:
                    value.extend(char.encode("utf-8"))
                    line += char == "\n"
            require(pos < len(source), f"line {line}: unterminated grammar string")
            pos += 1
            out.append(Meta("string" if quote == '"' else "char", value.decode("latin1"), begin_line))
        elif char.isascii() and (char.isalnum() or char == "_"):
            end = pos + 1
            while end < len(source) and source[end].isascii() and (source[end].isalnum() or source[end] in "_' "):
                if source[end] == " ": break
                end += 1
            out.append(Meta("name", source[pos:end], line))
            pos = end
        else:
            multi = next((x for x in ("::=", "...", "->", "=>", "\\/", "/\\") if source.startswith(x, pos)), None)
            out.append(Meta("punct", multi or char, line))
            pos += len(multi) if multi else 1
    return out


def N(name): return ("n", name)
def T(text): return ("t", text)
def O(item): return ("optional", item)
def L(minimum, element, separator=""): return ("list", minimum, element, separator)


def optional(item):
    return L(0, item[2], item[3]) if item[0] == "list" and item[1] == 1 else O(item)


def show(item):
    if item[0] == "n": return item[1]
    if item[0] == "t": return repr(item[1])
    if item[0] == "optional": return show(item[1]) + "?"
    return f"list{item[1]}({show(item[2])}, sep={item[3]!r})"


@dataclass(frozen=True)
class Rule:
    lhs: str
    rhs: tuple
    label: str
    module: str
    line: int


@dataclass
class Authored:
    rules: list
    macros: dict
    tokens: dict
    token_order: list
    comments: list
    entries: list
    hashes: dict


def category(ts, at):
    require(at < len(ts), "unexpected end of category")
    if ts[at].text == "[" and ts[at].kind == "punct":
        inner, at = category(ts, at + 1)
        require(at < len(ts) and ts[at].text == "]", "unterminated list category")
        return "[" + inner + "]", at + 1
    require(ts[at].kind == "name", f"line {ts[at].line}: expected category")
    return ts[at].text, at + 1


def statements(source):
    ts = meta_tokens(source)
    at = 0
    while at < len(ts):
        if ts[at].kind == "name" and ts[at].text == "Terms":
            at += 1
            require(at < len(ts) and ts[at].text == "{", "Terms requires braces")
            at += 1
            depth, begin = 0, at
            while at < len(ts):
                x = ts[at]
                if x.kind == "punct" and x.text == "}" and depth == 0:
                    require(begin == at, f"line {x.line}: unterminated Terms declaration")
                    break
                if x.kind == "punct":
                    if x.text in "{([": depth += 1
                    elif x.text in "})]": depth -= 1
                    elif x.text == ";" and depth == 0:
                        yield ts[begin:at]
                        begin = at + 1
                require(depth >= 0, f"line {x.line}: unbalanced Terms declaration")
                at += 1
            require(at < len(ts), "unterminated Terms block")
        at += 1


def read_authored(grammar_dir, overrides=None, modules=ISO_MODULES):
    rules, macros, tokens, token_order, comments, entries, hashes = [], {}, {}, [], [], [], {}
    overrides = overrides or {}
    for module in modules:
        path = grammar_dir / (module + ".module")
        source = overrides.get(module, path.read_text())
        hashes[path.name] = hashlib.sha256(source.encode()).hexdigest()
        for st in statements(source):
            require(st, f"{path.name}: empty grammar declaration")
            word, line = st[0].text, st[0].line
            if word == "token":
                require(len(st) >= 3 and st[1].kind == "name", "malformed token declaration")
                name = st[1].text
                require(name not in tokens, f"duplicate token {name}")
                tokens[name] = RegexParser(st[2:]).parse()
                token_order.append(name)
            elif word in {"separator", "terminator"}:
                at, minimum = 1, 0
                if st[at].text == "nonempty": at, minimum = at + 1, 1
                element, at = category(st, at)
                require(at == len(st)-1 and st[at].kind == "string", "malformed list macro")
                require(element not in macros, f"duplicate list macro for {element}")
                macros[element] = (minimum, st[at].text, word, path.name, line)
            elif word == "coercions":
                require(len(st) == 3 and st[1].kind == "name" and st[2].text.isdigit(), "malformed coercions")
                name, levels = st[1].text, int(st[2].text)
                require(0 < levels < 100, "unexpected coercion level range")
                previous = name
                for level in range(1, levels+1):
                    current = name + str(level)
                    rules.append(Rule(previous, (N(current),), "_", path.name, line))
                    previous = current
                rules.append(Rule(previous, (T("("), N(name), T(")")), "_", path.name, line))
            elif word == "comment":
                require(all(x.kind == "string" for x in st[1:]) and len(st) in {2,3}, "malformed comment declaration")
                comments.append(tuple(x.text for x in st[1:]))
            elif word == "entrypoints":
                require(all(x.kind == "name" or x.text == "," for x in st[1:]), "malformed entrypoints")
                entries.extend(x.text for x in st[1:] if x.kind == "name")
            else:
                require(len(st) > 3 and st[0].kind == "name" and st[1].text == ".", f"{path.name}:{line}: unsupported Terms declaration")
                lhs, at = category(st, 2)
                require(st[at].text == "::=", "expected ::=")
                at += 1
                rhs = []
                while at < len(st):
                    if st[at].kind == "string":
                        rhs.append(T(st[at].text))
                        at += 1
                    else:
                        name, at = category(st, at)
                        rhs.append(N(name))
                rules.append(Rule(lhs, tuple(rhs), word, path.name, line))
    return Authored(rules, macros, tokens, token_order, comments, entries, hashes)


def standard_grammar(data):
    names = {r["nonterminal"] for r in data["normative"]}
    grammar, citations = defaultdict(list), defaultdict(list)
    for row in data["normative"]:
        for p in row["productions"]:
            rhs = []
            for word in p["rhs"].split():
                if word in names: rhs.append(N(word))
                elif word.endswith("opt") and word[:-3] in names: rhs.append(O(N(word[:-3])))
                else: rhs.append(T(word))
            grammar[row["nonterminal"]].append(tuple(rhs))
            citations[row["nonterminal"]].append({"id": p["id"], "clause": row["clause"], "lines": p["lines"], "rhs": p["rhs"]})
    return dict(grammar), dict(citations)


# Regexes are interpreted independently over bytes. No native automaton is used.
EPS = ("eps",)
EMPTY = ("set", 0)
ALL_BYTES = (1 << 256) - 1


def rset(values): return ("set", sum(1 << x for x in set(values)))
def rtext(text): return rseq(*(rset([x]) for x in text.encode("latin1")))


def ralt(*values):
    flat = []
    mask = 0
    for value in values:
        children = value[1:] if value[0] == "alt" else (value,)
        for child in children:
            if child[0] == "set": mask |= child[1]
            elif child not in flat: flat.append(child)
    if mask: flat.append(("set", mask))
    if not flat: return EMPTY
    return flat[0] if len(flat) == 1 else ("alt", *flat)


def rseq(*values):
    flat = []
    for value in values:
        if value == EMPTY: return EMPTY
        if value == EPS: continue
        flat.extend(value[1:] if value[0] == "seq" else (value,))
    if not flat: return EPS
    return flat[0] if len(flat) == 1 else ("seq", *flat)


def rstar(value): return EPS if value in (EPS, EMPTY) else ("star", value)
def ropt(value): return ralt(EPS, value)
def rplus(value): return rseq(value, rstar(value))


class RegexParser:
    """The small authored LBNF Reg expression language, parsed from tokens."""
    def __init__(self, tokens): self.ts, self.at = tokens, 0

    def take(self, word):
        if self.at < len(self.ts) and self.ts[self.at].kind == "punct" and self.ts[self.at].text == word:
            self.at += 1
            return True
        return False

    def parse(self):
        value = self.union()
        require(self.at == len(self.ts), f"unconsumed regex token at {self.at}")
        return value

    def union(self):
        values = [self.sequence()]
        while self.take("|"): values.append(self.sequence())
        return ralt(*values)

    def sequence(self):
        values = []
        while self.at < len(self.ts):
            x = self.ts[self.at]
            if x.kind == "punct" and x.text in {"|", ")", "]", "}"}: break
            values.append(self.difference())
        require(values, "empty regex sequence; spell epsilon explicitly")
        return rseq(*values)

    def difference(self):
        value = self.postfix()
        while self.take("-"):
            other = self.postfix()
            require(value[0] == other[0] == "set", "coverage adapter requires byte-set regex subtraction")
            value = ("set", value[1] & ~other[1])
        return value

    def postfix(self):
        value = self.atom()
        while self.at < len(self.ts) and self.ts[self.at].kind == "punct":
            op = self.ts[self.at].text
            if op not in {"*", "+", "?"}: break
            self.at += 1
            value = {"*": rstar, "+": rplus, "?": ropt}[op](value)
        return value

    def atom(self):
        require(self.at < len(self.ts), "missing regex atom")
        if self.take("("):
            value = self.union()
            require(self.take(")"), "unterminated regex parentheses")
            return value
        for open_, close, kind in [("[", "]", "set"), ("{", "}", "text")]:
            if self.take(open_):
                x = self.ts[self.at]
                self.at += 1
                require(x.kind == "string" and self.take(close), "malformed regex string atom")
                return rset(x.text.encode("latin1")) if kind == "set" else rtext(x.text)
        x = self.ts[self.at]
        self.at += 1
        if x.kind == "char":
            require(len(x.text) == 1, "regex character must carry one byte")
            return rtext(x.text)
        builtin = {"eps": EPS, "char": ("set", ALL_BYTES),
                   "digit": rset(range(48,58)), "upper": rset(range(65,91)),
                   "lower": rset(range(97,123)),
                   "letter": rset([*range(65,91), *range(97,123)])}
        require(x.kind == "name" and x.text in builtin, f"unsupported regex atom {x.text!r}")
        return builtin[x.text]


class NFA:
    """Thompson construction plus lazy subset transitions, independent of C."""
    def __init__(self, regex):
        self.edges, self.epsilon = [], []
        self.start, self.finish = self.build(regex)
        self.mask_finish = 1 << self.finish
        self.closure_cache, self.move_cache = {}, {}

    def state(self):
        self.edges.append([])
        self.epsilon.append([])
        return len(self.edges)-1

    def build(self, regex):
        a, b = self.state(), self.state()
        kind = regex[0]
        if kind == "eps": self.epsilon[a].append(b)
        elif kind == "set":
            if regex[1]: self.edges[a].append((regex[1], b))
        elif kind in {"alt", "seq"}:
            previous = a
            for child in regex[1:]:
                x, y = self.build(child)
                if kind == "alt":
                    self.epsilon[a].append(x)
                    self.epsilon[y].append(b)
                else:
                    self.epsilon[previous].append(x)
                    previous = y
            if kind == "seq": self.epsilon[previous].append(b)
        elif kind == "star":
            x, y = self.build(regex[1])
            self.epsilon[a].extend([b, x])
            self.epsilon[y].extend([b, x])
        else: raise CoverageError(f"unknown regular expression {kind}")
        return a, b

    def closure_one(self, state):
        if state in self.closure_cache: return self.closure_cache[state]
        todo, seen, active = [state], set(), 0
        while todo:
            x = todo.pop()
            if x in seen: continue
            seen.add(x)
            if self.edges[x] or x == self.finish: active |= 1 << x
            todo.extend(self.epsilon[x])
        self.closure_cache[state] = active
        return active

    def move(self, states, byte):
        key = states, byte
        if key in self.move_cache: return self.move_cache[key]
        result, remaining = 0, states
        while remaining:
            bit = remaining & -remaining
            remaining -= bit
            state = bit.bit_length()-1
            for mask, target in self.edges[state]:
                if (mask >> byte) & 1: result |= self.closure_one(target)
        self.move_cache[key] = result
        return result


@lru_cache(maxsize=64)
def compare_regex(left, right, relation="equal"):
    """Decide equality/inclusion by the finite subset-product graph.

    The alphabet partition is exact: bytes share a class iff every edge mask in
    both NFAs treats them alike. A shortest distinguishing byte word is retained.
    """
    first, second = NFA(left), NFA(right)
    masks = set(mask for machine in (first, second) for row in machine.edges for mask, _ in row)
    classes = [ALL_BYTES]
    for mask in masks:
        split = []
        for block in classes:
            positive, negative = block & mask, block & ~mask
            if positive: split.append(positive)
            if negative: split.append(negative)
        classes = split
    alphabet = sorted((x & -x).bit_length()-1 for x in classes)
    initial = first.closure_one(first.start), second.closure_one(second.start)
    queue, previous = deque([initial]), {initial: None}
    while queue:
        state = queue.popleft()
        yes, other = bool(state[0] & first.mask_finish), bool(state[1] & second.mask_finish)
        wrong = yes != other if relation == "equal" else yes and not other
        if wrong:
            word, current = bytearray(), state
            while previous[current] is not None:
                current, byte = previous[current]
                word.append(byte)
            word.reverse()
            return {"ok": False, "witness_hex": word.hex(), "witness": repr(bytes(word)),
                    "left_accepts": yes, "right_accepts": other, "product_states": len(previous)}
        for byte in alphabet:
            successor = first.move(state[0], byte), second.move(state[1], byte)
            if successor not in previous:
                previous[successor] = state, byte
                queue.append(successor)
    return {"ok": True, "relation": relation, "product_states": len(previous),
            "alphabet_classes": len(alphabet), "nfa_states": [len(first.edges), len(second.edges)]}


def linear_regular_grammar(grammar, root, leaves=None, atom_adapter=None):
    """Solve direct left/right-linear recursion using Arden's decomposition.

    All indirect cycles and interior self references are refused. The standard
    lexical fragments and declaration-specifier fragments have this form.
    """
    leaves = leaves or {}
    cache, active = {}, set()

    def atom(item):
        if atom_adapter is not None:
            overridden = atom_adapter(item)
            if overridden is not None: return overridden
        if item[0] == "t": return rtext(item[1])
        if item[0] == "n": return category_regex(item[1])
        if item[0] == "optional": return ropt(atom(item[1]))
        if item[0] == "list":
            element, separator = atom(item[2]), rtext(item[3])
            body = rseq(element, rstar(rseq(separator, element)))
            return body if item[1] else ropt(body)
        raise CoverageError(f"unknown regular grammar atom {item}")

    def category_regex(name):
        if name in leaves: return leaves[name]
        if name in cache: return cache[name]
        require(name in grammar, f"unbound regular grammar category {name}")
        require(name not in active, f"indirect regular grammar cycle through {name}")
        active.add(name)
        base, prefix, suffix = [], [], []
        for rhs in grammar[name]:
            # Optional direct recursion denotes its two ordinary productions.
            for branch in expand_optional(rhs):
                positions = [i for i,x in enumerate(branch) if x == N(name)]
                if not positions: base.append(rseq(*(atom(x) for x in branch)))
                else:
                    require(len(positions) == 1, f"nonlinear regular grammar {name}")
                    pos = positions[0]
                    if pos == 0: suffix.append(rseq(*(atom(x) for x in branch[1:])))
                    elif pos == len(branch)-1: prefix.append(rseq(*(atom(x) for x in branch[:-1])))
                    else: raise CoverageError(f"interior recursive category {name}")
        require(not (prefix and suffix), f"mixed recursion direction in {name}")
        value = ralt(*base)
        if prefix: value = rseq(rstar(ralt(*prefix)), value)
        if suffix: value = rseq(value, rstar(ralt(*suffix)))
        active.remove(name)
        cache[name] = value
        return value

    return category_regex(root)


def expand_optional(rhs):
    branches = [()]
    for item in rhs:
        options = [(), (item[1],)] if item[0] == "optional" else [(item,)]
        branches = [prefix+suffix for prefix in branches for suffix in options]
    return branches


class PhraseComparison:
    def __init__(self, authored, standard, citations, refactorings, profile):
        self.authored, self.std, self.citations, self.ref = authored, standard, citations, refactorings
        self.by_lhs = defaultdict(list)
        for rule in authored.rules: self.by_lhs[rule.lhs].append(rule)
        duplicates = [name for name,rows in self.by_lhs.items() if len({r.rhs for r in rows}) != len(rows)]
        require(not duplicates, f"duplicate authored source alternatives in {duplicates}")
        self.names = refactorings["native_category_names"]
        self.namespace = refactorings["namespace_categories"]
        self.optional_names = refactorings["optional_categories"]
        self.alias_names = refactorings["plain_aliases"]
        self.inline = set(refactorings["inline_standard_categories"])
        self.used_macros, self.verified_aux, self.list_details = set(), set(), []
        self.std_lists = {}
        self.profile = profile
        self.check_profile()
        self.extract_lists()
        self.check_interfaces()

    def check_profile(self):
        ts = meta_tokens(self.profile)
        spelling, pairs, classifications = [], [], []
        for at,x in enumerate(ts):
            if x.kind == "name" and x.text == "lexical" and at+5 < len(ts):
                tail = ts[at+1:at+6]
                if tail[0].text == "spelling":
                    require(tail[1].kind == tail[3].kind == "string" and tail[2].text == "as",
                            "malformed lexical spelling profile interface")
                    spelling.append((tail[1].text, tail[3].text))
                elif tail[0].text == "pair":
                    require(tail[1].kind == tail[2].kind == tail[4].kind == "string" and tail[3].text == "as",
                            "malformed lexical pair profile interface")
                    pairs.append((tail[1].text, tail[2].text, tail[4].text))
            elif x.kind == "name" and x.text == "classification":
                classifications.append(tuple(t.text for t in ts[at+1:at+6]))
        expected = {("<:", "["), (":>", "]"), ("<%", "{"), ("%>", "}"),
                    ("%:", "#"), ("%:%:", "##")}
        require(set(spelling) == expected and len(spelling) == len(expected),
                "profile digraph spellings differ from ISO 6.4.6p3")
        require(pairs == [("_Atomic", "(", "<atomic-parenthesis>")],
                "profile _Atomic lexical-pair interface differs from 6.7.2.4")
        ns = self.namespace
        require(classifications == [(ns["token"], ns["ordinary_marker"], ns["typedef_marker"], "using", "CScope")],
                "profile classification interface differs from the declared namespace adapter")
        self.spellings = dict(spelling)

    def extract_lists(self):
        for name in self.ref["standard_list_categories"]:
            choices = self.std[name]
            require(len(choices) == 2, f"reference list {name} no longer has two alternatives")
            base = [x for x in choices if N(name) not in x]
            recursive = [x for x in choices if N(name) in x]
            require(len(base) == len(recursive) == 1, f"reference list {name} has unexpected recursion")
            element, cons = base[0], recursive[0]
            if cons[0] == N(name) and cons[-len(element):] == element:
                between = cons[1:-len(element)]
                direction = "left"
            elif cons[-1] == N(name) and cons[:len(element)] == element:
                between = cons[len(element):-1]
                direction = "right"
            else: raise CoverageError(f"reference list {name} changes its element in recursive alternative")
            require(all(x[0] == "t" for x in between), f"reference list {name} separator is not terminal-only")
            separator = "".join(x[1] for x in between)
            if len(element) == 1 and element[0][0] == "n": item = element[0]
            else:
                require(name == "initializer-list", f"undeclared compound list element for {name}")
                self.std["@initializer-element"] = [element]
                item = N("@initializer-element")
            self.std_lists[name] = L(1, item, separator)
            self.list_details.append({"standard": name, "element": [show(x) for x in element],
                                      "separator": separator, "minimum": 1, "reference_direction": direction,
                                      "clause": self.citations[name][0]["clause"]})

    def std_atom(self, item):
        kind = item[0]
        if kind == "t": return [(item,)]
        if kind == "optional":
            child = item[1]
            if child[0] == "n" and child[1] in self.std_lists:
                return [(optional(self.std_lists[child[1]]),)]
            return [(), *self.std_atom(child)]
        require(kind == "n", "unexpected reference phrase atom")
        name = item[1]
        if name in self.std_lists: return [(self.std_lists[name],)]
        if name == "string-literal": return [(L(1, N("@string-literal-atom")),)]
        if name in self.inline:
            return [branch for rhs in self.std[name] for branch in self.std_rhs(rhs)]
        return [(item,)]

    def std_rhs(self, rhs):
        branches = [()]
        for item in rhs:
            branches = [prefix+suffix for prefix in branches for suffix in self.std_atom(item)]
        return branches

    def mapped_name(self, name):
        if name in self.names: return self.names[name]
        if name in self.alias_names: return self.alias_names[name]
        if name in set(self.namespace.values())-{"<ordinary-name>", "<typedef-name>", "CIdent"}: return "identifier"
        raise CoverageError(f"unmapped authored phrase category {name}")

    def native_list(self, name):
        require(name in self.authored.macros, f"list category [{name}] has no authored macro")
        self.used_macros.add(name)
        minimum, separator, kind, module, line = self.authored.macros[name]
        require(kind == "separator" or separator == "", f"{module}:{line}: a terminator retains a trailing separator")
        return L(minimum, N(self.mapped_name(name)), separator)

    def native_atom(self, item):
        if item[0] == "t":
            return [(T("(") if item[1] == "<atomic-parenthesis>" else item,)]
        require(item[0] == "n", "unexpected authored phrase atom")
        name = item[1]
        if name.startswith("["):
            return [(self.native_list(name[1:-1]),)]
        if name in self.optional_names:
            target = self.optional_names[name]
            if target in self.std_lists: return [(optional(self.std_lists[target]),)]
            return [(), (N(target),)]
        return [(N(self.mapped_name(name)),)]

    def native_rhs(self, rhs):
        branches = [()]
        for item in rhs:
            branches = [prefix+suffix for prefix in branches for suffix in self.native_atom(item)]
        return branches

    def assert_rules(self, name, expected):
        actual = [rule.rhs for rule in self.by_lhs[name]]
        require(Counter(actual) == Counter(expected),
                f"auxiliary interface {name}: expected {list(map(str, expected))}, found {list(map(str, actual))}")
        self.verified_aux.add(name)

    def check_interfaces(self):
        ns = self.namespace
        self.assert_rules(ns["ordinary"], [(N(ns["token"]), T(ns["ordinary_marker"]))])
        self.assert_rules(ns["typedef"], [(N(ns["token"]), T(ns["typedef_marker"]))])
        self.assert_rules(ns["general"], [(N(ns["ordinary"]),), (N(ns["general_typedef"]),)])
        self.assert_rules(ns["general_typedef"], [(N(ns["typedef"]),)])
        self.assert_rules("BoundDeclarator", [(N("Declarator"),)])
        self.assert_rules("OldParameterName", [(N(ns["ordinary"]),)])
        self.assert_rules("TypeSpecifier", [(N("UniqueTypeSpecifier"),), (N("PrimitiveTypeSpecifier"),)])
        for name, target in self.optional_names.items():
            actual = [r.rhs for r in self.by_lhs[name]]
            require(len(actual) == 2 and actual.count(()) == 1,
                    f"optional interface {name} must have exactly empty and nonempty alternatives")
            some = next(x for x in actual if x)
            require(len(some) == 1, f"optional interface {name} must wrap one category")
            if target in self.std_lists:
                require(some[0][0] == "n" and some[0][1].startswith("["), f"optional list interface {name} lost its list")
                found = self.native_list(some[0][1][1:-1])
                require(found == self.std_lists[target],
                        f"optional list interface {name}: {show(found)} differs from {show(self.std_lists[target])}")
            else:
                require(self.native_rhs(some) == [(N(target),)], f"optional interface {name} changed its nonempty child")
            self.verified_aux.add(name)

    def check_partitions(self):
        actual_unique = set(branch for rule in self.by_lhs["UniqueTypeSpecifier"] for branch in self.native_rhs(rule.rhs))
        expected_unique = set()
        raw_unique = self.ref["unique_standard_type_alternatives"]
        for rhs in self.std["type-specifier"]:
            if len(rhs) == 1 and rhs[0][1] in raw_unique: expected_unique.update(self.std_rhs(rhs))
        require(actual_unique == expected_unique, "unique/primitive type-specifier partition changed its clause-6.7.2 classes")
        result = []
        for part in self.ref["constraint_partitions"]:
            name = part["standard"]
            non_type = []
            for rhs in self.std[name]:
                require(len(rhs) == 2 and rhs[1] == O(N(name)), f"reference specifier recursion {name} changed")
                if rhs[0] != N("type-specifier"): non_type.append((rhs[0],))
            if part["non_type"] == "NonTypeSpecifier":
                actual = [branch for r in self.by_lhs[part["non_type"]] for branch in self.native_rhs(r.rhs)]
                require(Counter(actual) == Counter(non_type), "non-type specifier alternatives differ from normative grammar")
            else:
                require(non_type == [(N(self.mapped_name(part["non_type"])),)], "specifier-qualifier non-type alphabet changed")
            native_grammar = {cat: [r.rhs for r in rules] for cat,rules in self.by_lhs.items()}
            # Expand real list macro language, retaining minimum and order.
            def adapter(item):
                if item[0] != "n" or not item[1].startswith("["): return None
                macro_name = item[1][1:-1]
                self.used_macros.add(macro_name)
                require(macro_name in self.authored.macros, f"missing specifier list macro {macro_name}")
                minimum, sep, kind, _, _ = self.authored.macros[macro_name]
                element = linear_regular_grammar(native_grammar, macro_name, leaves, adapter)
                require(sep == "", "specifier tail macro acquired separator/trailing punctuation")
                return rplus(element) if minimum else rstar(element)
            leaves = {part["unique"]: rtext("U"), part["primitive"]: rtext("P"),
                      part["non_type"]: rtext("N")}
            actual = linear_regular_grammar(native_grammar, part["root"], leaves, adapter)
            expected = rseq(rstar(rtext("N")),
                            ralt(rseq(rtext("U"), rstar(rtext("N"))),
                                 rseq(rtext("P"), rstar(ralt(rtext("P"), rtext("N"))))))
            comparison = compare_regex(expected, actual)
            require(comparison["ok"], f"{name}: partial 6.7.2p2 specifier language mismatch: {comparison}")
            self.verified_aux.update(part["native_categories"])
            result.append({"standard": name, "source_alternatives": len(self.std[name]),
                           "constraint": part["constraint"], "condition": part["condition"], **comparison})
        return result

    def compare(self):
        partition_checks = self.check_partitions()
        phrase_names = {name for name in self.std if self.citations.get(name, [{}])[0].get("clause", "").startswith(("6.5", "6.7", "6.8", "6.9"))}
        phrase_names.add("@initializer-element")
        expected, origins = set(), defaultdict(list)
        for name in phrase_names:
            if name in self.inline or name in self.std_lists or name in {x["standard"] for x in self.ref["constraint_partitions"]}: continue
            for i,rhs in enumerate(self.std[name]):
                for branch in self.std_rhs(rhs):
                    key = name, branch
                    expected.add(key)
                    if name in self.citations: origins[key].append(self.citations[name][i])
        expected.add(("translation-unit", (self.std_lists["translation-unit"],)))
        actual, sources = Counter(), defaultdict(list)
        for rule in self.authored.rules:
            if rule.lhs in self.verified_aux: continue
            lhs = self.mapped_name(rule.lhs)
            for rhs in self.native_rhs(rule.rhs):
                key = lhs, rhs
                actual[key] += 1
                sources[key].append({"module": rule.module, "line": rule.line, "label": rule.label, "source_category": rule.lhs})
        missing, invented = expected-set(actual), set(actual)-expected
        duplicates = {k: n for k,n in actual.items() if n > 1}
        def present(key):
            lhs,rhs = key
            return {"lhs": lhs, "rhs": [show(x) for x in rhs], "source": sources.get(key, []),
                    "standard": origins.get(key, self.citations.get(lhs, []))}
        report = {"normalized_expected": len(expected), "normalized_authored": len(actual),
                  "missing": [present(k) for k in sorted(missing, key=str)],
                  "invented": [present(k) for k in sorted(invented, key=str)],
                  "duplicates": [{**present(k), "multiplicity": n} for k,n in duplicates.items()],
                  "declaration_specifiers": partition_checks, "separated_lists": self.list_details,
                  "verified_auxiliary_categories": sorted(self.verified_aux),
                  "ordered_rhs_mapping": [{**present(k)} for k in sorted(expected & set(actual), key=str)]}
        unused = set(self.authored.macros)-self.used_macros
        require(not unused, f"unaccounted authored list macros {sorted(unused)}")
        return report


def interval_regex(low, high, domains, encode_digit):
    """A lexicographic fixed-width interval, independently factored into boxes."""
    require(len(low) == len(high) == len(domains) and tuple(low) <= tuple(high), "malformed encoded interval")
    if not low: return EPS
    lo, hi = low[0], high[0]
    if lo == hi:
        return rseq(encode_digit([lo]), interval_regex(low[1:], high[1:], domains[1:], encode_digit))
    tail_min = [min(x) for x in domains[1:]]
    tail_max = [max(x) for x in domains[1:]]
    lower = rseq(encode_digit([lo]), interval_regex(low[1:], tail_max, domains[1:], encode_digit))
    upper = rseq(encode_digit([hi]), interval_regex(tail_min, high[1:], domains[1:], encode_digit))
    middle_values = [x for x in domains[0] if lo < x < hi]
    middle = rseq(encode_digit(middle_values), *(encode_digit(x) for x in domains[1:]))
    return ralt(lower, middle, upper)


def scalar_ucn_regex(ranges):
    def hex_digit(values):
        return rset(ord(c) for x in values for c in (str(x) if x < 10 else chr(87+x)+chr(55+x)))
    alternatives = []
    for width, prefix in [(4, "\\u"), (8, "\\U")]:
        for low, high in ranges:
            high = min(high, (1 << (width*4))-1)
            if low > high: continue
            low_digits = [int(c,16) for c in f"{low:0{width}x}"]
            high_digits = [int(c,16) for c in f"{high:0{width}x}"]
            alternatives.append(rseq(rtext(prefix), interval_regex(low_digits, high_digits,
                                [range(16)]*width, hex_digit)))
    return ralt(*alternatives)


def utf8_scalar_regex(ranges):
    alternatives = []
    for low,high in ranges:
        for floor,ceiling in [(0x80,0x7ff),(0x800,0xffff),(0x10000,0x10ffff)]:
            a,b = max(low,floor),min(high,ceiling)
            if a > b: continue
            begin,end = list(chr(a).encode("utf-8")),list(chr(b).encode("utf-8"))
            alternatives.append(interval_regex(begin,end,[range(256)]+[range(128,192)]*(len(begin)-1),rset))
    return ralt(*alternatives)


def subtract_intervals(ranges, excluded):
    result = list(ranges)
    for a,b in excluded:
        updated = []
        for x,y in result:
            if y < a or b < x: updated.append((x,y))
            else:
                if x < a: updated.append((x,a-1))
                if b < y: updated.append((b+1,y))
        result = updated
    return result


class LexicalComparison:
    def __init__(self, authored, standard, citations, data, refactorings, phrase):
        self.actual, self.std, self.citations = authored, standard, citations
        self.data, self.ref, self.phrase = data, refactorings, phrase
        self.ranges = [(r["low"],r["high"]) for r in data["annex_D"]["allowed"]]
        excluded = [(r["low"],r["high"]) for r in data["annex_D"]["noninitial"]]
        self.initial_ranges = subtract_intervals(self.ranges, excluded)
        self.literal_ranges = refactorings["byte_carrier_profile"]["literal_ucn_scalar_ranges"]
        self.visited = set()

    def source_regex(self, name, leaves=None):
        # English alternatives are source-character profile interfaces, not
        # literal English words in the regular language.
        leaves = dict(leaves or {})
        for carrier,quote in [("c-char", "'"), ("s-char", '"')]:
            choices = self.std[carrier]
            descriptions = [rhs for rhs in choices if " ".join(x[1] for x in rhs).startswith("any member of the source character set except")]
            require(len(descriptions) == 1 and (N("escape-sequence"),) in choices and len(choices) == 2,
                    f"source-character profile interface {carrier} differs from N1570")
            ordinary = ("set", ALL_BYTES & ~rset([ord(quote), 92, *self.ref["byte_carrier_profile"]["literal_excluded_bytes"]])[1])
            # Only inject the actual descriptive alternative, retaining escapes
            # and all recursive c-char/s-char productions from the reference.
            leaves["@"+carrier+"-ordinary"] = ordinary
        adapted = dict(self.std)
        for carrier in ("c-char", "s-char"):
            adapted[carrier] = [(N("@"+carrier+"-ordinary"),), (N("escape-sequence"),)]
        def mark(item):
            if item[0] == "n" and item[1] in self.std: self.visited.add(item[1])
            return None
        self.visited.add(name)
        return linear_regular_grammar(adapted, name, leaves, mark)

    def nondigit(self, ranges, constrained_ucn=True):
        require(self.std["identifier-nondigit"] == [(N("nondigit"),), (N("universal-character-name"),),
                    tuple(T(x) for x in "other implementation-defined characters".split())],
                "reference identifier-nondigit interface changed")
        self.visited.add("identifier-nondigit")
        self.visited.update({"universal-character-name", "hex-quad"})
        ucn = scalar_ucn_regex(ranges) if constrained_ucn else self.source_regex("universal-character-name")
        return ralt(self.source_regex("nondigit"), ucn, utf8_scalar_regex(ranges))

    def identifier_regex(self):
        choices = self.std["identifier"]
        require(choices == [(N("identifier-nondigit"),), (N("identifier"), N("identifier-nondigit")),
                            (N("identifier"), N("digit"))], "reference identifier recursion changed")
        self.visited.add("identifier")
        return rseq(self.nondigit(self.initial_ranges),
                    rstar(ralt(self.nondigit(self.ranges), self.source_regex("digit"))))

    def lexical_check(self, native_name, reference_name, expected):
        require(native_name in self.actual.tokens, f"missing authored lexical token {native_name}")
        comparison = compare_regex(expected, self.actual.tokens[native_name])
        return {"native_token": native_name, "standard": reference_name,
                "citations": self.citations[reference_name], **comparison}

    def compare(self):
        token_names = {"CInteger", "CFloat", "CCharacter", "CString", "CIdent", "CPPNumber", "CReservedKeyword"}
        require(set(self.actual.tokens) == token_names, f"undeclared lexical interface tokens {set(self.actual.tokens)^token_names}")
        require(self.actual.comments == [("//",), ("/*", "*/")], "comment interface differs from ISO 6.4.9")
        require(self.actual.entries == ["TranslationUnit"], "entrypoint interface differs from external-definition translation unit")
        # Check the UCN source production before constraining its value domain.
        require(self.std["universal-character-name"] == [(T("\\u"), N("hex-quad")),
                    (T("\\U"), N("hex-quad"), N("hex-quad"))], "reference UCN production changed")
        require(self.std["hex-quad"] == [(N("hexadecimal-digit"),)*4], "reference hex-quad arity changed")
        checks = [self.lexical_check("CInteger", "integer-constant", self.source_regex("integer-constant")),
                  self.lexical_check("CFloat", "floating-constant", self.source_regex("floating-constant")),
                  self.lexical_check("CIdent", "identifier", self.identifier_regex())]
        literal_leaves = {"universal-character-name": scalar_ucn_regex(self.literal_ranges)}
        self.visited.update({"universal-character-name", "hex-quad"})
        for native,name in [("CCharacter", "character-constant"), ("CString", "string-literal")]:
            checks.append(self.lexical_check(native,name,self.source_regex(name,literal_leaves)))
        words = {r[0][1] for r in self.std["keyword"]}
        require(all(len(r) == 1 and r[0][0] == "t" for r in self.std["keyword"]), "reference keyword alternatives are not singleton terminals")
        punctuation = {r[0][1] for r in self.std["punctuator"]}
        require(all(len(r) == 1 and r[0][0] == "t" for r in self.std["punctuator"]), "reference punctuator alternatives are not singleton terminals")
        virtual = {"<ordinary-name>", "<typedef-name>", "<atomic-parenthesis>"}
        terminals = {x[1] for rule in self.actual.rules for x in rule.rhs if x[0] == "t" and x[1] not in virtual}
        terminals.update({"_Atomic", "(", *self.phrase.spellings, *self.phrase.spellings.values()})
        actual_words = {x for x in terminals if x and (x[0].isalpha() or x[0] == "_")}
        actual_punctuation = terminals-actual_words
        require(actual_words <= words, f"extra authored keyword terminals {actual_words-words}")
        require(actual_punctuation == punctuation, f"punctuator coverage missing={punctuation-actual_punctuation}, invented={actual_punctuation-punctuation}")
        reserved = ralt(*(rtext(x) for x in words-actual_words))
        checks.append(self.lexical_check("CReservedKeyword", "keyword", reserved))
        require(self.actual.token_order.index("CReservedKeyword") < self.actual.token_order.index("CIdent"),
                "reserved-keyword rejection must precede identifier ties")
        self.visited.update({"keyword", "punctuator", "token", "constant", "enumeration-constant"})
        require(self.std["token"] == [(N(x),) for x in ("keyword", "identifier", "constant", "string-literal", "punctuator")],
                "reference token union changed")
        require(self.std["constant"] == [(N(x),) for x in ("integer-constant", "floating-constant", "enumeration-constant", "character-constant")],
                "reference constant union changed")
        require(self.std["enumeration-constant"] == [(N("identifier"),)], "enumeration-constant alias changed")
        # pp-number is quarantined: inclusion is required, equality is not.
        # A syntax failure token may conservatively collect malformed byte runs.
        pp_leaves = {"identifier-nondigit": self.nondigit(self.ranges, constrained_ucn=False)}
        guard = compare_regex(self.source_regex("pp-number", pp_leaves), self.actual.tokens["CPPNumber"], "subset")
        guard = {"native_token": "CPPNumber", "standard": "pp-number", "condition": self.ref["cpp_number_guard"]["condition"],
                 "citations": self.citations["pp-number"], **guard}
        require(not any(x == N("CPPNumber") for r in self.actual.rules for x in r.rhs), "pp-number guard acquired a successful phrase production")
        for valid in ("CInteger", "CFloat"):
            require(self.actual.token_order.index(valid) < self.actual.token_order.index("CPPNumber"),
                    f"valid {valid} must precede pp-number rejection on equal length")
        post_lexical = {r["nonterminal"] for r in self.data["normative"] if r["scope"] == "post_preprocessing_lexical"}
        unvisited = post_lexical-self.visited
        require(not unvisited, f"unaccounted normative lexical fragments {unvisited}")
        return {"exact_language_checks": checks, "rejected_pp_number_guard": guard,
                "keyword_spellings": len(words), "punctuator_spellings": len(punctuation),
                "annex_D": {"allowed_intervals": len(self.ranges), "noninitial_intervals": len(self.data["annex_D"]["noninitial"])},
                "visited_lexical_nonterminals": sorted(post_lexical)}


class PreprocessingComparison:
    """ISO 6.10 and the preprocessing-token grammar against the authored
    CPreprocessing module: ordered productions after the declared adapters,
    exact header-name, punctuator, new-line, lparen and other languages, and
    containment of the standard identifier, pp-number and literal languages."""

    def __init__(self, authored, standard, citations, data, refactorings, lexical):
        self.authored, self.std, self.citations, self.data = authored, standard, citations, data
        self.ref = refactorings["preprocessing"]
        self.lexical = lexical
        self.names = self.ref["native_category_names"]
        self.optional_names = self.ref["optional_categories"]
        self.tokens = self.ref["token_categories"]
        self.markers = {k: v for k,v in self.ref["classification_markers"].items() if k.startswith("<")}
        self.operand = self.ref["conditional_operand"]
        self.by_lhs = defaultdict(list)
        for rule in authored.rules: self.by_lhs[rule.lhs].append(rule)
        self.std_lists, self.used_macros, self.verified_aux = {}, set(), set()
        for name in self.ref["standard_list_categories"]:
            choices = self.std[name]
            base = [x for x in choices if N(name) not in x]
            recursive = [x for x in choices if N(name) in x]
            require(len(base) == len(recursive) == 1 and len(base[0]) == 1 and recursive[0] == (N(name),)+base[0],
                    f"reference list {name} is not a left-recursive list of one element")
            self.std_lists[name] = L(1, base[0][0], "")
        scope = [r for r in data["normative"] if r["scope"].startswith("preprocessing_")]
        self.scope = {r["nonterminal"]: r for r in scope}
        other = [rhs for rhs in self.std["preprocessing-token"] if all(x[0] == "t" for x in rhs) and len(rhs) > 3]
        require(len(other) == 1, "reference preprocessing-token prose alternative changed")
        self.other_rhs = other[0]

    def std_atom(self, item):
        if item[0] == "t": return [(item,)]
        if item[0] == "optional":
            child = item[1]
            if child[0] == "n" and child[1] in self.std_lists: return [(optional(self.std_lists[child[1]]),)]
            return [(), *self.std_atom(child)]
        name = item[1]
        if name == self.operand["standard"]: return [(self.std_lists[self.operand["native"]],)]
        if name in self.std_lists: return [(self.std_lists[name],)]
        return [(item,)]

    def std_rhs(self, name, rhs):
        if name == "preprocessing-token" and rhs == self.other_rhs: return [(N("@other"),)]
        branches = [()]
        for item in rhs: branches = [a+b for a in branches for b in self.std_atom(item)]
        return branches

    def mapped(self, name):
        if name in self.names: return self.names[name]
        if name in self.tokens: return self.tokens[name]
        raise CoverageError(f"unmapped authored preprocessing category {name}")

    def native_atom(self, item):
        if item[0] == "t":
            return [()] if item[1] in self.markers else [(item,)]
        name = item[1]
        if name.startswith("["):
            element = name[1:-1]
            require(element in self.authored.macros, f"list category [{element}] has no authored macro")
            self.used_macros.add(element)
            minimum, separator, kind, module, line = self.authored.macros[element]
            require(kind == "separator" or separator == "", f"{module}:{line}: a terminator retains a trailing separator")
            return [(L(minimum, N(self.mapped(element)), separator),)]
        if name in self.optional_names:
            target = self.optional_names[name]
            if target in self.std_lists: return [(optional(self.std_lists[target]),)]
            return [(), (N(target),)]
        return [(N(self.mapped(name)),)]

    def native_rhs(self, rhs):
        branches = [()]
        for item in rhs: branches = [a+b for a in branches for b in self.native_atom(item)]
        return branches

    def check_interfaces(self):
        for name, target in self.optional_names.items():
            actual = [r.rhs for r in self.by_lhs[name]]
            require(len(actual) == 2 and actual.count(()) == 1, f"optional interface {name} must have exactly empty and nonempty alternatives")
            some = next(x for x in actual if x)
            require(len(some) == 1, f"optional interface {name} must wrap one category")
            if target in self.std_lists:
                found = self.native_atom(some[0])
                require(found == [(self.std_lists[target],)], f"optional list interface {name} differs from {target}")
            else:
                require(self.native_rhs(some) == [(N(target),)], f"optional interface {name} changed its nonempty child")
            self.verified_aux.add(name)
        # Each marker opens exactly its own group-closing line, before the #.
        for marker, owner in self.markers.items():
            uses = [(r.lhs, i) for r in self.authored.rules for i,x in enumerate(r.rhs) if x == T(marker)]
            require(uses and all(self.mapped(lhs) == owner and i == 0 for lhs,i in uses),
                    f"marker {marker} must only open {owner} productions")
            require(all(r.rhs[:2] == (T(marker), T("#")) for r in self.by_lhs[next(k for k,v in self.names.items() if v == owner)]),
                    f"every {owner} production must open with {marker} #")

    def compare_phrases(self):
        self.check_interfaces()
        lexical_names = {"header-name", "h-char-sequence", "h-char", "q-char-sequence", "q-char", "pp-number",
                         "new-line", "lparen"}
        expected, origins = set(), defaultdict(list)
        # Phrase categories the directives use from clause 6.7 (identifier-list)
        # are compared with the preprocessing rules that author them.
        token_names = set(self.tokens.values())
        names, pending = set(), [n for n in self.scope if n not in lexical_names and n not in self.std_lists]
        while pending:
            name = pending.pop()
            if name in names: continue
            names.add(name)
            for rhs in self.std[name]:
                for item in rhs:
                    child = item[1] if item[0] == "optional" else item
                    if child[0] == "n" and child[1] not in lexical_names and child[1] not in self.std_lists \
                            and child[1] not in token_names and child[1] != self.operand["standard"]:
                        pending.append(child[1])
        for name in sorted(names):
            for i,rhs in enumerate(self.std[name]):
                for branch in self.std_rhs(name, rhs):
                    expected.add((name, branch)); origins[(name, branch)].append(self.citations[name][i])
        actual, sources = Counter(), defaultdict(list)
        for rule in self.authored.rules:
            if rule.lhs in self.verified_aux: continue
            lhs = self.mapped(rule.lhs)
            for rhs in self.native_rhs(rule.rhs):
                actual[(lhs, rhs)] += 1
                sources[(lhs, rhs)].append({"module": rule.module, "line": rule.line, "label": rule.label})
        missing, invented = expected-set(actual), set(actual)-expected
        duplicates = {k: n for k,n in actual.items() if n > 1}
        def present(key):
            lhs,rhs = key
            return {"lhs": lhs, "rhs": [show(x) for x in rhs], "source": sources.get(key, []),
                    "standard": origins.get(key, self.citations.get(lhs, []))}
        unused = set(self.authored.macros)-self.used_macros
        require(not unused, f"unaccounted authored preprocessing list macros {sorted(unused)}")
        return {"normalized_expected": len(expected), "normalized_authored": len(actual),
                "missing": [present(k) for k in sorted(missing, key=str)],
                "invented": [present(k) for k in sorted(invented, key=str)],
                "duplicates": [{**present(k), "multiplicity": n} for k,n in duplicates.items()],
                "verified_auxiliary_categories": sorted(self.verified_aux)}

    def compare_tokens(self):
        authored = self.authored.tokens
        require(set(authored) == set(self.tokens), f"undeclared preprocessing tokens {set(authored)^set(self.tokens)}")
        require(not self.authored.comments, "comments are phase 3 white space, not part of the directive grammar")
        require(self.authored.entries == ["PreprocessingFile"], "entrypoint interface differs from preprocessing-file")
        excluded = self.lexical.ref["byte_carrier_profile"]["literal_excluded_bytes"]
        def chars(close): return ("set", ALL_BYTES & ~rset([close, *excluded])[1])
        header = linear_regular_grammar(self.std, "header-name", {"h-char": chars(ord(">")), "q-char": chars(ord('"'))})
        for name in ("header-name", "h-char-sequence", "h-char", "q-char-sequence", "q-char"):
            require(name in self.scope, f"reference {name} missing")
        punctuation = {r[0][1] for r in self.std["punctuator"]}
        white = rset([ord(x) for x in " \t\n\v\f\r"])
        exact = [("CPPHeaderName", "header-name", header),
                 ("CPPPunctuator", "punctuator", ralt(*(rtext(x) for x in sorted(punctuation)))),
                 ("CPPNewLine", "new-line", rtext("\n")),
                 ("CPPLParen", "lparen", rtext("(")),
                 ("CPPOther", "preprocessing-token", ("set", ALL_BYTES & ~white[1]))]
        checks = []
        for native, name, language in exact:
            checks.append({"native_token": native, "standard": name, "citations": self.citations[name],
                           **compare_regex(language, authored[native]), "relation": "equal"})
        lex = self.lexical
        pp_leaves = {"identifier-nondigit": lex.nondigit(lex.ranges, constrained_ucn=False)}
        literal_leaves = {"universal-character-name": scalar_ucn_regex(lex.literal_ranges)}
        contained = [("CPPIdentifier", "identifier", lex.identifier_regex()),
                     ("CPPNumber", "pp-number", lex.source_regex("pp-number", pp_leaves)),
                     ("CPPCharacter", "character-constant", lex.source_regex("character-constant", literal_leaves)),
                     ("CPPString", "string-literal", lex.source_regex("string-literal", literal_leaves))]
        for native, name, language in contained:
            checks.append({"native_token": native, "standard": name, "citations": self.citations[name],
                           **compare_regex(language, authored[native], "subset"), "relation": "contains"})
        return {"checks": checks,
                "conditions": [self.ref["other_token"], self.ref["lparen_condition"], self.ref["inclusion_tokens"],
                               self.operand, self.ref["classification_markers"]["condition"]]}


def verify_reference(data, source=None):
    normative = Counter((r["clause"],r["nonterminal"],p["rhs"])
                        for r in data["normative"] for p in r["productions"])
    annex = Counter((r["clause"],r["nonterminal"],p["rhs"])
                    for r in data["annex"] for p in r["productions"])
    require(normative == annex, "independent Clause-6 extraction differs from Annex A cross-check")
    names = [r["nonterminal"] for r in data["normative"]]
    require(len(names) == len(set(names)), "reference contains duplicate nonterminal headers")
    post = [r for r in data["normative"] if r["scope"].startswith("post_preprocessing_")]
    require(len(post) == 120 and sum(len(r["productions"]) for r in post) == 520,
            "post-preprocessing C11 extraction is not the intended 120 nonterminals / 520 alternatives")
    preprocessing = [r for r in data["normative"] if r["scope"].startswith("preprocessing_")]
    require(len(preprocessing) == 23 and sum(len(r["productions"]) for r in preprocessing) == 57,
            "preprocessing C11 extraction is not the intended 23 nonterminals / 57 alternatives")
    included = post+preprocessing
    source_checked = False
    if source is not None:
        carrier = source.read_bytes()
        require(hashlib.sha256(carrier).hexdigest() == data["source"]["sha256"], "N1570 text carrier hash differs from independent extraction")
        # pdftotext form-feed bytes are page markers, not line boundaries in
        # the independently recorded carrier line numbers.
        lines = carrier.decode("utf-8").split("\n")
        for rows in (data["normative"], data["annex"]):
            for row in rows:
                header = re.sub(r"^\s*\d+\s+", "", lines[row["head_line"]-1]).strip()
                if header.startswith("("):
                    require(header.startswith("("+row["clause"]+")"), "Annex A clause annotation changed")
                    header = header.split(")",1)[1].strip()
                require(header == row["nonterminal"]+":"+(" one of" if row["one_of"] else ""),
                        f"reference header does not match original N1570 line {row['head_line']}")
                if row["one_of"]:
                    per_line = defaultdict(list)
                    for p in row["productions"]:
                        require(len(p["lines"]) == 1, "one-of alternative has unexpected continuation")
                        per_line[p["lines"][0]].append(p["rhs"])
                    for line,words in per_line.items():
                        require([x for x in lines[line-1].split() if x != "∗"] == words,
                                f"reference one-of extraction differs at source line {line}")
                else:
                    for p in row["productions"]:
                        actual = " ".join(" ".join(lines[line-1].split()) for line in p["lines"])
                        require(actual == p["rhs"], f"reference RHS differs at source lines {p['lines']}")
        for key in ("allowed", "noninitial"):
            for row in data["annex_D"][key]:
                found = [(int(a,16),int(b or a,16)) for a,b in re.findall(r"\b([0-9A-F]{4,6})(?:\s*[–−-]\s*([0-9A-F]{4,6}))?\b",lines[row["line"]-1])]
                require((row["low"],row["high"]) in found, f"Annex D interval differs from source line {row['line']}")
        d1 = [i for i,line in enumerate(lines) if line.strip() == "D.1 Ranges of characters allowed"]
        d2 = [i for i,line in enumerate(lines) if line.strip() == "D.2 Ranges of characters disallowed initially"]
        require(len(d1) == len(d2) == 1 and d1[0] < d2[0], "normative Annex D table headings are ambiguous")
        end = next(i for i in range(d2[0]+1,len(lines)) if "\f" in lines[i])
        for key,begin,finish in [("allowed",d1[0]+1,d2[0]), ("noninitial",d2[0]+1,end)]:
            extracted = []
            for i in range(begin,finish):
                for a,b in re.findall(r"\b([0-9A-F]{4,6})(?:\s*[–−-]\s*([0-9A-F]{4,6}))?\b",lines[i]):
                    extracted.append({"low": int(a,16), "high": int(b or a,16), "line": i+1})
            require(extracted == data["annex_D"][key], f"normative Annex D {key} extraction omitted or invented intervals")
        source_checked = True
    return {"normative_nonterminals": len(names), "normative_alternatives": sum(normative.values()),
            "annex_cross_check": True, "source_sha256": data["source"]["sha256"],
            "original_carrier_checked": source_checked,
            "included_nonterminals": len(included), "included_alternatives": sum(len(r["productions"]) for r in included),
            "excluded": [{"nonterminal": r["nonterminal"], "clause": r["clause"], "scope": r["scope"],
                           "alternatives": len(r["productions"])} for r in data["normative"] if r not in included]}


def audit(grammar_dir, data, refactorings, profile, overrides=None):
    try:
        authored = read_authored(grammar_dir, overrides)
        standard,citations = standard_grammar(data)
        phrase = PhraseComparison(authored,standard,citations,refactorings,profile)
        phrase_report = phrase.compare()
        output = {"ok": False, "module_sha256": authored.hashes, "phrase": phrase_report}
        if any(phrase_report[x] for x in ("missing", "invented", "duplicates")):
            output["failure_stage"] = "ordered phrase productions"
            return output
        lexical = LexicalComparison(authored,standard,citations,data,refactorings,phrase)
        lexical_report = lexical.compare()
        output["lexical"] = lexical_report
        checks = lexical_report["exact_language_checks"]+[lexical_report["rejected_pp_number_guard"]]
        output["ok"] = all(row["ok"] for row in checks)
        if not output["ok"]:
            output["failure_stage"] = "lexical regular languages"
            return output
        pp_authored = read_authored(grammar_dir, overrides, (refactorings["preprocessing"]["module"],))
        output["module_sha256"].update(pp_authored.hashes)
        pp = PreprocessingComparison(pp_authored,standard,citations,data,refactorings,lexical)
        pp_phrase = pp.compare_phrases()
        output["preprocessing"] = {"phrase": pp_phrase}
        if any(pp_phrase[x] for x in ("missing", "invented", "duplicates")):
            output["ok"], output["failure_stage"] = False, "preprocessing productions"
            return output
        pp_tokens = pp.compare_tokens()
        output["preprocessing"]["tokens"] = pp_tokens
        if not all(row["ok"] for row in pp_tokens["checks"]):
            output["ok"], output["failure_stage"] = False, "preprocessing token languages"
        # Every original production keeps its original citation and explicit
        # transformation boundary, including alternatives distributed by inlining.
        accounting = []
        for row in data["normative"]:
            if not row["scope"].startswith("post_preprocessing_"): continue
            name = row["nonterminal"]
            if row["scope"] == "post_preprocessing_lexical": method = "independent token-language comparison / lexical union interface"
            elif name in phrase.std_lists: method = "extracted nonempty list language; authored minimum/separator/element checked"
            elif name in phrase.inline: method = "reference RHS distributed into actual parent productions"
            elif name in {p["standard"] for p in refactorings["constraint_partitions"]}: method = "regular-language equality conditional on partial 6.7.2p2 restriction"
            else: method = "ordered RHS comparison after declared name/optional/list adapters"
            accounting.append({"nonterminal": name, "clause": row["clause"], "scope": row["scope"],
                               "method": method, "productions": row["productions"]})
        if output["ok"]:
            lexical_pp = {"header-name", "h-char-sequence", "h-char", "q-char-sequence", "q-char", "pp-number", "new-line", "lparen"}
            for row in data["normative"]:
                if not row["scope"].startswith("preprocessing_"): continue
                name = row["nonterminal"]
                if name in lexical_pp: method = "token-language comparison (equal, or containing for pp-number)"
                elif name in pp.std_lists: method = "extracted nonempty list language; authored minimum/separator/element checked"
                else: method = "ordered RHS comparison after declared name/optional/list/marker/operand adapters"
                accounting.append({"nonterminal": name, "clause": row["clause"], "scope": row["scope"],
                                   "method": method, "productions": row["productions"]})
        output["original_production_accounting"] = accounting
        output["conditions"] = [refactorings["phase6_string_lists"], refactorings["byte_carrier_profile"],
                                {"clause": "6.7.2p2", "condition": "Complete legal primitive-type multisets, declaration constraints and typing remain external."}]
        output["claims_excluded"] = refactorings["claims_excluded"]
        return output
    except CoverageError as error:
        return {"ok": False, "failure_stage": "source/interface admission", "error": str(error)}


def corruption_controls(grammar_dir, data, refactorings, profile):
    sources = {module: (grammar_dir/(module+".module")).read_text() for module in ISO_MODULES}
    controls = []
    def run(name, module, modified, expected_ok=False):
        result = audit(grammar_dir,data,refactorings,profile,{module: modified})
        require(result["ok"] == expected_ok, f"corruption control {name}: expected {expected_ok}, got {result['ok']}")
        row = {"name": name, "expected_admission": expected_ok, "observed_admission": result["ok"],
               "mutation_sha256": hashlib.sha256(modified.encode()).hexdigest()}
        if not result["ok"]:
            row["failure_stage"] = result.get("failure_stage")
            if "error" in result: row["diagnostic"] = result["error"]
            if "phrase" in result:
                row["differences"] = {k: result["phrase"][k] for k in ("missing", "invented", "duplicates")}
            if "lexical" in result:
                row["differences"] = [x for x in result["lexical"]["exact_language_checks"]+[result["lexical"]["rejected_pp_number_guard"]] if not x["ok"]]
        controls.append(row)
    def replace(name,module,before,after,expected_ok=False):
        source = sources[module]
        require(source.count(before) == 1, f"control {name}: mutation anchor no longer unique")
        run(name,module,source.replace(before,after),expected_ok)
    replace("changed arithmetic operator","CExpr",'Add . Expr11 ::= Expr11 "+" Expr12;', 'Add . Expr11 ::= Expr11 "**" Expr12;')
    replace("ordered constructor arguments","CExpr",'Subscript . Expr15 ::= Expr15 "[" Expr "]";', 'Subscript . Expr15 ::= Expr "[" Expr15 "]";')
    replace("omitted alternative","CExpr",'Remainder . Expr12 ::= Expr12 "%" Expr13;', '')
    replace("invented alternative","CExpr",'Add . Expr11 ::= Expr11 "+" Expr12;', 'Add . Expr11 ::= Expr11 "+" Expr12;\n Extra . Expr11 ::= Expr11 "**" Expr12;')
    replace("nullable enumerator list","CDecl",'separator nonempty Enumerator ",";', 'separator Enumerator ",";')
    replace("changed initializer separator","CDecl",'separator nonempty InitializerElement ",";', 'separator nonempty InitializerElement ";";')
    replace("nullable type-qualifier list","CDecl",'separator nonempty TypeQualifier "";', 'separator TypeQualifier "";')
    replace("nonempty block item list","CStmt",'terminator BlockItem "";', 'terminator nonempty BlockItem "";')
    replace("missing optional empty alternative","CExpr",'NoArguments . Arguments ::=;', '')
    replace("wrong specifier partition","CDecl",'PrimitiveSpecifierTail . PrimitiveOrNonTypeSpecifier ::= PrimitiveTypeSpecifier;', 'PrimitiveSpecifierTail . PrimitiveOrNonTypeSpecifier ::= UniqueTypeSpecifier;')
    replace("extra type in unique tail","CDecl",'UniqueDeclarationSpecifiers . DeclarationSpecifiers ::= UniqueTypeSpecifier [NonTypeSpecifier];', 'UniqueDeclarationSpecifiers . DeclarationSpecifiers ::= UniqueTypeSpecifier [PrimitiveOrNonTypeSpecifier];')
    replace("wrong old parameter namespace","CDecl",'_ . OldParameterName ::= COrdinaryName;', '_ . OldParameterName ::= CGeneralName;')
    replace("duplicate source alternative","CExpr",'Add . Expr11 ::= Expr11 "+" Expr12;', 'Add . Expr11 ::= Expr11 "+" Expr12;\n DuplicateAdd . Expr11 ::= Expr11 "+" Expr12;')
    replace("constructor labels are diagnostic","CExpr",'Add . Expr11 ::= Expr11 "+" Expr12;', 'RenamedAdd . Expr11 ::= Expr11 "+" Expr12;',True)
    source = sources["CExpr"]
    first = 'Add . Expr11 ::= Expr11 "+" Expr12;'
    second = 'Subtract . Expr11 ::= Expr11 "-" Expr12;'
    require(first in source and second in source, "alternative-order control anchors missing")
    modified = source.replace(first,"ALTERNATIVE_ORDER_SENTINEL").replace(second,first).replace("ALTERNATIVE_ORDER_SENTINEL",second)
    run("source alternative ordering is irrelevant","CExpr",modified,True)
    def alter_token(name,native,extra,expected_ok=False):
        pattern = re.compile(r"(\btoken "+re.escape(native)+r"\s+)(.*?)(;)", re.DOTALL)
        found = list(pattern.finditer(sources["CLex"]))
        require(len(found) == 1, f"lexical corruption anchor {native} missing")
        match = found[0]
        value = "("+match[2]+" | "+extra+")"
        modified = sources["CLex"][:match.start(2)]+value+sources["CLex"][match.end(2):]
        run(name,"CLex",modified,expected_ok)
    alter_token("invalid identifier UCN range","CIdent",'{"\\\\u0041"}')
    alter_token("forbidden literal UCN scalar","CString",'{"\\\"\\\\uD800\\\""}')
    alter_token("invented integer token spelling","CInteger",'{"0b1"}')
    alter_token("equivalent integer regex alternative","CInteger",'{"1"}',True)
    replace("omitted pp-number signed-exponent continuation","CLex",' | ["eEpP"] ["+-"]', '')
    pp_source = (grammar_dir/"CPreprocessing.module").read_text()
    def pp_replace(name,before,after,expected_ok=False):
        require(pp_source.count(before) == 1, f"control {name}: mutation anchor no longer unique")
        run(name,"CPreprocessing",pp_source.replace(before,after),expected_ok)
    pp_replace("omitted control-line alternative",'Undef . ControlLine ::= "#" "undef" CPPIdentifier CPPNewLine;','')
    pp_replace("invented directive",'NullDirective . ControlLine ::= "#" CPPNewLine;',
               'NullDirective . ControlLine ::= "#" CPPNewLine;\n      Invented . ControlLine ::= "#" "invented" CPPNewLine;')
    pp_replace("optional non-directive tokens",'NonDirective . NonDirective ::= [PPToken] CPPNewLine;',
               'NonDirective . NonDirective ::= OptionalPPTokens CPPNewLine;')
    pp_replace("reordered if-section",'IfSection . IfSection ::= IfGroup [ElifGroup] OptionalElseGroup EndifLine;',
               'IfSection . IfSection ::= IfGroup OptionalElseGroup [ElifGroup] EndifLine;')
    pp_replace("misplaced group marker",'EndifLine . EndifLine ::= "<endif-line>" "#" "endif" CPPNewLine;',
               'EndifLine . EndifLine ::= "#" "<endif-line>" "endif" CPPNewLine;')
    pp_replace("header-name crossing lines","token CPPHeaderName (('<' (char - [\"\\n\\r\\x00>\"])+ '>')",
               "token CPPHeaderName (('<' (char - [\"\\r\\x00>\"])+ '>')")
    pp_replace("missing digraph punctuator",'{"%:%:"} | ','')
    pp_replace("pp-number without signed exponents",' | (["eEpP"] ["+-"])','')
    pp_replace("preprocessing labels are diagnostic",'Undef . ControlLine','RenamedUndef . ControlLine',True)
    # Independent controls of the reference-side regular-language engine itself.
    for name,a,b,expected in [
        ("regex distributivity",ralt(rtext("ab"),rtext("ac")),rseq(rtext("a"),ralt(rtext("b"),rtext("c"))),True),
        ("regex ordered repetition",rplus(rtext("xy")),rseq(rtext("xy"),rstar(rtext("xy"))),True),
        ("regex argument order separates",rtext("ab"),rtext("ba"),False),
        ("regex epsilon differs from empty",EPS,EMPTY,False),
        ("regex suffix distinguishes",rstar(rtext("x")),rplus(rtext("x")),False)]:
        result = compare_regex(a,b)
        require(result["ok"] == expected, f"independent language-engine control {name} failed")
        controls.append({"name": name, "expected_equivalence": expected, **result})
    return controls


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grammar-dir",type=Path,default=ROOT/"grammar")
    parser.add_argument("--source",type=Path,help="verify original N1570 text carrier and cited lines")
    parser.add_argument("--report",type=Path,help="write full machine-readable comparison and original citations")
    parser.add_argument("--self-test",action="store_true",help="also run independent source-corruption and regular-language controls")
    args = parser.parse_args(argv)
    data = json.loads((DATA/"n1570-grammar.json").read_text())
    ref = json.loads((DATA/"n1570-refactorings.json").read_text())
    default_source = ROOT/"build"/"n1570.txt"
    source = args.source or (default_source if default_source.exists() else None)
    try:
        reference = verify_reference(data,source)
        profile = (args.grammar_dir/"C11.profile").read_text()
        result = audit(args.grammar_dir,data,ref,profile)
        result["reference"] = reference
        if args.self_test and result["ok"]:
            result["controls"] = corruption_controls(args.grammar_dir,data,ref,profile)
        if args.report:
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.write_text(json.dumps(result,indent=2)+"\n")
        if result["ok"]:
            controls = len(result.get("controls", []))
            pp = result["preprocessing"]
            equal = sum(row["relation"] == "equal" for row in pp["tokens"]["checks"])
            contained = sum(row["relation"] == "contains" for row in pp["tokens"]["checks"])
            print(f"N1570 coverage: 143 nonterminals / 577 original alternatives; "
                  f"{result['phrase']['normalized_expected']} ordered phrase signatures and "
                  f"{pp['phrase']['normalized_expected']} preprocessing signatures; "
                  f"missing=0 invented=0; 6 exact lexical checks + rejected pp-number inclusion; "
                  f"preprocessing tokens {equal} exact + {contained} containing; "
                  f"{controls} controls; declared conditional boundaries apply.")
            return 0
        print("N1570 coverage refused: "+result.get("error",result.get("failure_stage","unknown mismatch")),file=sys.stderr)
        for row in result.get("phrase",{}).get("missing",[])[:6]: print("missing: "+str(row),file=sys.stderr)
        for row in result.get("phrase",{}).get("invented",[])[:6]: print("invented: "+str(row),file=sys.stderr)
        for row in result.get("lexical",{}).get("exact_language_checks",[]):
            if not row["ok"]: print("lexical: "+str(row),file=sys.stderr)
        for row in result.get("preprocessing",{}).get("phrase",{}).get("missing",[])[:6]: print("preprocessing missing: "+str(row),file=sys.stderr)
        for row in result.get("preprocessing",{}).get("phrase",{}).get("invented",[])[:6]: print("preprocessing invented: "+str(row),file=sys.stderr)
        for row in result.get("preprocessing",{}).get("tokens",{}).get("checks",[]):
            if not row["ok"]: print("preprocessing token: "+str(row),file=sys.stderr)
        return 1
    except (CoverageError,OSError,ValueError) as error:
        print("N1570 coverage refused: "+str(error),file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
