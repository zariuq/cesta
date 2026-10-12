#!/usr/bin/env python3
"""A checked, reversible human view of Cesta C trees (reference implementation).

No C text is generated here. The supplied Cesta executable owns C lexing,
concrete syntax, tree validation and printing. Python only presents/expands trees.
This is not a C type checker, macro expander, or preprocessing frontend.
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

class SurfaceError(ValueError):
    pass

class Sym(str):
    """An unevaluated MeTTa symbol, not a host-language number."""

@dataclass(frozen=True)
class Text:
    data: bytes

S = Any

def sy(x: str) -> Sym:
    return Sym(x)

def txt(x: str | bytes) -> Text:
    return Text(x.encode('utf-8') if isinstance(x, str) else x)

def c(h: str, *xs: S) -> list[S]:
    return [sy('c:' + h), *xs]

def sf(h: str, *xs: S) -> list[S]:
    return [sy(h), *xs]

def cl(xs=()) -> list[S]:
    return c('List', *xs)

def hd(x: S) -> str:
    return str(x[0]) if isinstance(x, list) and x and isinstance(x[0], Sym) else ''

def isc(x: S, h: str) -> bool:
    return hd(x) == 'c:' + h

def need(ok: bool, message: str) -> None:
    if not ok:
        raise SurfaceError(message)

def arity(x: list, lo: int, hi: int | None = None) -> None:
    hi = lo if hi is None else hi
    need(lo <= len(x)-1 <= hi, f'{hd(x)} expects {lo}' +
         (f'..{hi}' if hi != lo else '') + ' operands')

def parse(data: bytes) -> list[S]:
    """Byte-preserving S-expression reader; no numeric grounding or evaluation."""
    p, n = 0, len(data)
    def skip():
        nonlocal p
        while p < n:
            if data[p] in b' \t\r\n\v\f': p += 1
            elif data[p] == 59:
                while p < n and data[p] not in b'\r\n': p += 1
            else: break
    def one(depth=0):
        nonlocal p
        need(depth < 512, 'surface nesting exceeds 512 levels')
        skip(); need(p < n, 'unexpected end of input')
        if data[p] == 40:
            p += 1; out = []
            while True:
                skip(); need(p < n, 'unclosed parenthesis')
                if data[p] == 41: p += 1; return out
                out.append(one(depth+1))
        need(data[p] != 41, f'unexpected closing parenthesis at byte {p}')
        if data[p] == 34:
            p += 1; out = bytearray()
            while p < n and data[p] != 34:
                a = data[p]; p += 1
                if a != 92:
                    out.append(a); continue
                need(p < n, 'unfinished string escape')
                a = data[p]; p += 1
                if a in b'"\\': out.append(a)
                elif a in b'nrt': out.append({110:10,114:13,116:9}[a])
                elif a == 120:
                    need(p+2 <= n and re.fullmatch(b'[0-9a-fA-F]{2}',data[p:p+2]) is not None,
                         'host \\x escape must have exactly two hex digits')
                    out.append(int(data[p:p+2],16)); p += 2
                else: raise SurfaceError(f'unsupported host string escape \\{chr(a)}')
            need(p < n, 'unclosed string'); p += 1; return Text(bytes(out))
        start = p
        while p < n and data[p] not in b' \t\r\n\v\f();"': p += 1
        need(p > start, f'bad token at byte {p}')
        try: return Sym(data[start:p].decode('utf-8'))
        except UnicodeDecodeError as e: raise SurfaceError('non-UTF8 symbol; use (id "...") or byte strings') from e
    forms=[]
    while True:
        skip()
        if p == n: return forms
        forms.append(one())

def quote_bytes(b: bytes) -> str:
    out='"'
    for a in b:
        if a in (34,92): out += '\\' + chr(a)
        elif a in (10,13,9): out += {10:'\\n',13:'\\r',9:'\\t'}[a]
        elif a < 32 or a >= 127: out += f'\\x{a:02x}'
        else: out += chr(a)
    return out+'"'

def dumps(x: S) -> str:
    if isinstance(x, Text): return quote_bytes(x.data)
    if isinstance(x, Sym): return str(x)
    need(isinstance(x,list), 'not an S-expression')
    return '('+' '.join(map(dumps,x))+')'

def pretty(x: S, indent=0, width=100) -> str:
    flat=dumps(x)
    if not isinstance(x,list) or not x or len(flat)+indent <= width: return flat
    h=hd(x)
    lead={'fn':4,'fn-old':5,'for':4,'while':2,'if':2,'switch':2,
          'var':3,'return':1,'block':1,'unit':1,'struct':2,'union':2,'enum':2}.get(h,1)
    lead=min(lead,len(x))
    prefix='('+' '.join(dumps(a) for a in x[:lead])
    if len(prefix)+indent > width and lead > 1: lead=1; prefix='('+dumps(x[0])
    return prefix+''.join('\n'+' '*(indent+2)+pretty(a,indent+2,width) for a in x[lead:])+')'

PRIMITIVES=dict(zip('char short int long float double signed unsigned _Complex __int128 __signed__'.split(),
                    'Char Short Int Long Float Double Signed Unsigned Complex GNUInt128 GNUSigned'.split()))
UNIQUE=dict(zip('void _Bool __auto_type _Float128 _Float32 _Float64 _Float32x _Float64x _Float128x __float128'.split(),
               'Void Bool GNUAutoType GNUFloat128 GNUFloat32 GNUFloat64 GNUFloat32x GNUFloat64x GNUFloat128x GNUQuad'.split()))
QUALIFIERS=dict(zip('const restrict volatile _Atomic __const__ __const __volatile__ __volatile __restrict__ __restrict'.split(),
                   'Const Restrict Volatile AtomicQualifier GNUConst GNUConstPrefix GNUVolatile GNUVolatilePrefix GNURestrict GNURestrictPrefix'.split()))
STORAGE=dict(zip('typedef extern static _Thread_local auto register __thread'.split(),
                'Typedef Extern Static ThreadLocal Auto Register GNUThread'.split()))
FSPEC=dict(zip('inline _Noreturn __inline__ __inline'.split(),
              'Inline Noreturn GNUInline GNUInlinePrefix'.split()))
KEYWORDS=set(PRIMITIVES)|set(UNIQUE)|set(QUALIFIERS)|set(STORAGE)|set(FSPEC)|set('struct union enum if else switch case default while do for goto continue break return sizeof _Alignof _Alignas _Static_assert _Generic asm __asm__ __asm typeof __typeof__ __typeof __extension__ __attribute__ __attribute'.split())
BIN=dict(zip('* / % + - << >> < > <= >= == != & ^ | && || = *= /= %= += -= <<= >>= &= ^= |= ,'.split(),
             'Multiply Divide Remainder Add Subtract ShiftLeft ShiftRight Less Greater LessEqual GreaterEqual Equal NotEqual BitAnd BitXor BitOr And Or Assign MultiplyAssign DivideAssign RemainderAssign AddAssign SubtractAssign ShiftLeftAssign ShiftRightAssign BitAndAssign BitXorAssign BitOrAssign Comma'.split()))
UN=dict(zip('++ -- & * + - ~ ! sizeof post++ post--'.split(),
            'PreIncrement PreDecrement AddressOf Indirection UnaryPlus Negate BitNot Not SizeofExpression PostIncrement PostDecrement'.split()))
EXTRA_EXPR={'[]':('Subscript',['Expr','Expr']),'.':('Member',['Expr','Name']),
 '->':('PointerMember',['Expr','Name']),'?:':('Conditional',['Expr','Expr','Expr']),
 'cast':('Cast',['TypeName','Expr']),'sizeof-type':('SizeofType',['TypeName']),
 'alignof':('Alignof',['TypeName']),'label-address':('GNULabelAddress',['Name']),
 'va-arg':('GNUVaArg',['Expr','TypeName']),
 'types-compatible':('GNUCompatibleTypes',['TypeName','TypeName']),
 'convert-vector':('GNUConvertVector',['Expr','TypeName']),
 'extension':('GNUExpressionExtension',['Expr'])}
STMT_SORT={'If':'SelectionStatement','IfElse':'SelectionStatement','Switch':'SelectionStatement',
 'While':'IterationStatement','DoWhile':'IterationStatement','For':'IterationStatement',
 'ForDeclaration':'IterationStatement','Return':'JumpStatement','Break':'JumpStatement',
 'Continue':'JumpStatement','Goto':'JumpStatement','GNUIndirectGoto':'JumpStatement',
 'Label':'LabeledStatement','Case':'LabeledStatement','Default':'LabeledStatement','GNUCaseRange':'LabeledStatement'}
STMT_HEADS={'block','if','switch','while','do','for','return','break','continue','goto','goto*','label','case','default','case-range','empty'}
DECL_HEADS={'var','vars','typedef','static-assert','struct','union','enum'}
TYPE_HEADS={'*','[]','->','struct','union','enum','_Atomic','typeof','typeof-type'}
RESERVED_EXPR=set(BIN)|set(UN)|set(EXTRA_EXPR)|STMT_HEADS|DECL_HEADS|{'lit','id','str','strings','call','generic','compound','stmt-expr','fn','fn-old','unit','bytes','at'}

# These names are grounded rather than kept symbolic by the supplied CeTTa reader.
HOST_GROUNDED_NAMES = {'True', 'False', 'PI', 'EXP'}

def byte_payload(x: S) -> Text:
    if isinstance(x,Text): return x
    need(hd(x)=='bytes' and len(x)==2 and isinstance(x[1],Text),
         'expected a byte string or (bytes "hexadecimal-byte-pairs")')
    b=x[1].data
    need(re.fullmatch(b'(?:[0-9a-fA-F]{2})*',b) is not None,
         'bytes requires an even number of hexadecimal digits')
    return Text(bytes.fromhex(b.decode('ascii')))

def surface_bytes(b: bytes) -> S:
    # The actual CeTTa reader does not decode host \xHH escapes. Hex data uses
    # an ordinary ASCII string, and is interpreted by this codec, not MeTTa.
    return Text(b) if all(32<=a<127 or a in (9,10,13) for a in b) else sf('bytes',Text(b.hex().encode('ascii')))

def ast_identifier(b: bytes) -> S:
    # Match Cesta's serializer, including its boolean-name special case.
    if re.fullmatch(b'[A-Za-z_][A-Za-z_0-9]*',b) and b not in (b'True',b'False') and b.decode('ascii') not in KEYWORDS:
        return sy(b.decode('ascii'))
    return c('Symbol',Text(b))

def ident(x: S) -> S:
    if hd(x) in ('id','c:Symbol'):
        arity(x,1); spelling=byte_payload(x[1])
        return c('Symbol',spelling) if hd(x)=='c:Symbol' else ast_identifier(spelling.data)
    need(isinstance(x,Sym) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',x) is not None and x not in KEYWORDS,
         f'expected C identifier, got {dumps(x)}; use (id "...") for escaped names')
    need(x not in HOST_GROUNDED_NAMES, 'MeTTa-grounded identifier needs (id "'+str(x)+'")')
    return x

def surface_name(t: S) -> S:
    if isc(t,'Symbol'):
        candidate=sf('id',surface_bytes(t[1].data))
        return candidate if ident(candidate)==t else c('Symbol',surface_bytes(t[1].data))
    return sf('id',Text(str(t).encode('ascii'))) if t in HOST_GROUNDED_NAMES else t

def c_string(b: bytes) -> bytes:
    """One normalized ordinary C string; exact unusual spellings use lit."""
    out=bytearray(b'"')
    for a in b:
        if a in (34,92): out += b'\\'+bytes([a])
        elif a in (10,13,9): out += {10:b'\\n',13:b'\\r',9:b'\\t'}[a]
        elif a < 32 or a >= 127: out += ('\\%03o'%a).encode()
        else: out.append(a)
    out += b'"'; return bytes(out)

def numeric(b: bytes) -> S:
    # Classification only. Cesta's lexer subsequently validates the entire token.
    # Hex digits e/E do not make a hex integer a float; p/P does.
    s=b.decode('ascii')
    need(s and s[0] not in '+-', 'C numeric tokens do not include a leading sign; use unary +/-')
    isfloat=(any(a in s for a in '.pP') if s.lower().startswith('0x') else any(a in s for a in '.eE'))
    return c('FloatConst' if isfloat else 'IntConst',Text(b))

class Codec:
    def __init__(self, schema: dict):
        self.schema=schema
        self.cats={v['name']:v['sort'] for v in schema['categories']}
        self.shapes={v['name']:v['shape'] for v in schema['sorts']}
        self.constructors={}
        for p in schema['productions']:
            if p['projection']['kind']=='constructor':
                label=p['projection']['head']
                fields=[self.cats[a['category']] for a in p['rhs'] if a['kind']=='argument']
                desc=(self.cats[p['resultCategory']],fields)
                need(label not in self.constructors or self.constructors[label]==desc,
                     f'nonunique constructor {label}')
                self.constructors[label]=desc
        self.name_sort=next(s for s,sh in self.shapes.items() if sh['kind']=='token' and sh['representation']=='identifier')
        self.calls=0

    def rep(self,s: str) -> str:
        return self.name_sort if s=='Name' else self.cats.get(s,s)

    def lower(self,x: S,s: str) -> S:
        s=self.rep(s); self.calls+=1
        shape=self.shapes[s]
        if isinstance(x,Sym) and x.startswith('$'):
            raise SurfaceError('unfilled template hole '+x)
        if shape['kind']=='token':
            if shape['representation']=='identifier': return ident(x)
            return byte_payload(x)
        h=hd(x)
        if shape['kind']=='list':
            need(isinstance(x,list),f'{s} requires a list')
            seq=x[1:] if h=='c:List' else x
            return cl(self.lower(a,shape['element']) for a in seq)
        if h.startswith('c:'):
            label=h[2:]; need(label in self.constructors,f'unknown constructor {h}')
            result,fields=self.constructors[label]
            need(result==s,f'{h} has sort {result}, expected {s}')
            arity(x,len(fields))
            return c(label,*(self.lower(a,t) for a,t in zip(x[1:],fields)))
        if s=='Expr': return self.expr(x)
        if s=='TypeName':
            specs,d=self.typed(x,c('Declarator',c('NameDeclarator',sy('__compact_type_hole'))),False)
            return c('TypeName',specs,self.erase(d))
        if s=='DeclarationSpecifiers': return self.specs(x,True)
        if s=='SpecifierQualifiers': return self.specs(x,False)
        if s=='TypeQualifier':
            need(isinstance(x,Sym) and x in QUALIFIERS,f'unknown qualifier {dumps(x)}')
            return c(QUALIFIERS[x])
        if s=='OptionalFullExpression': return c('NoFullExpression') if x==[] else c('SomeFullExpression',self.lower(x,'Expr'))
        if s=='OptionalExpression': return c('NoExpression') if x==[] else c('SomeExpression',self.lower(x,'Expr'))
        if s=='Initializer': return self.init(x)
        if s=='InitializerElement':
            if h=='at':
                arity(x,2); need(isinstance(x[1],list),'at needs a designator path')
                return c('DesignatedInitializerElement',cl(self.lower(a,'Designator') for a in x[1]),self.lower(x[2],'Initializer'))
            return c('InitializerElement',self.lower(x,'Initializer'))
        if s=='Designator':
            if h=='.': arity(x,1); return c('MemberDesignator',ident(x[1]))
            if h=='[]': arity(x,1); return c('ArrayDesignator',self.lower(x[1],'Expr'))
        if s=='GenericAssociation':
            need(isinstance(x,list) and len(x)==2,'generic association is (Type expression) or (default expression)')
            return c('GenericDefault',self.lower(x[1],'Expr')) if x[0]==sy('default') else c('GenericType',self.lower(x[0],'TypeName'),self.lower(x[1],'Expr'))
        if s=='Declaration': return self.declaration(x)
        if s=='Parameter': return self.parameter(x)
        if s=='ParameterTypeList': return self.parameters(x)
        if s=='StructDeclaration': return self.field(x)
        if s=='Enumerator':
            if isinstance(x,Sym): return c('Enumerator',ident(x))
            need(isinstance(x,list) and len(x)==2,'enumerator is name or (name expression)')
            return c('EnumeratorValue',ident(x[0]),self.lower(x[1],'Expr'))
        if s=='StaticAssertion':
            need(h=='static-assert','expected static-assert'); arity(x,2)
            return c('StaticAssertion',self.lower(x[1],'Expr'),self.strings(x[2]))
        if s=='Block':
            need(h=='block','expected (block ...)')
            return c('Block',cl(self.lower(a,'BlockItem') for a in x[1:]))
        if s=='BlockItem':
            return c('BlockDeclaration',self.lower(x,'Declaration')) if h in DECL_HEADS else c('BlockStatement',self.lower(x,'Statement'))
        if s=='Statement': return self.statement(x)
        if s in {'SelectionStatement','IterationStatement','JumpStatement','LabeledStatement'}:
            t=self.statement(x)
            need(len(t)==2 and hd(t)=='c:'+s,f'{dumps(x)} is not a {s}')
            return t[1]
        if s=='FunctionDefinition': return self.function(x)
        if s=='ExternalDeclaration':
            return c('ExternalFunction',self.lower(x,'FunctionDefinition')) if h in {'fn','fn-old'} else c('ExternalDeclaration',self.lower(x,'Declaration'))
        if s=='TranslationUnit':
            need(h=='unit','expected (unit ...)')
            return c('TranslationUnit',cl(self.lower(a,'ExternalDeclaration') for a in x[1:]))
        raise SurfaceError(f'no compact production for {s}: {dumps(x)}; use its generated c: constructor')

    def expr(self,x: S) -> S:
        if isinstance(x,Text): return c('StringLiterals',cl([Text(c_string(x.data))]))
        if isinstance(x,Sym):
            if re.match(r'^[0-9]|^\.[0-9]|^#',x): return numeric((x[1:] if x.startswith('#') else x).encode())
            return c('Var',ident(x))
        need(isinstance(x,list) and bool(x),'empty list is not a C expression')
        h=hd(x); n=len(x)-1
        if h=='id': return c('Var',ident(x))
        if h=='lit':
            arity(x,1); token=byte_payload(x[1]); b=token.data
            if re.match(b'^(?:u8|[LuU])?"',b): return c('StringLiterals',cl([token]))
            if re.match(b"^[LuU]?'",b): return c('CharConst',token)
            return numeric(b)
        if h=='str':
            arity(x,1); return self.expr(byte_payload(x[1]))
        if h=='strings': return c('StringLiterals',self.strings(x))
        if h in UN and n==1: return c(UN[h],self.lower(x[1],'Expr'))
        if h in BIN and n==2: return c(BIN[h],self.lower(x[1],'Expr'),self.lower(x[2],'Expr'))
        if h in EXTRA_EXPR:
            tag,sorts=EXTRA_EXPR[h]; arity(x,len(sorts))
            return c(tag,*(self.lower(a,t) for a,t in zip(x[1:],sorts)))
        if h=='generic':
            need(n>=2,'generic needs a control expression and associations')
            return c('Generic',self.lower(x[1],'Expr'),cl(self.lower(a,'GenericAssociation') for a in x[2:]))
        if h=='stmt-expr': return c('GNUStatementExpression',self.lower(sf('block',*x[1:]),'Block'))
        if h=='compound':
            arity(x,2); it=self.lower(x[2],'Initializer')
            labels={'c:InitializerList':'CompoundLiteral','c:InitializerListComma':'CompoundLiteralComma','c:GNUEmptyInitializer':'GNUEmptyCompoundLiteral'}
            need(hd(it) in labels,'compound requires an initializer list')
            return c(labels[hd(it)],self.lower(x[1],'TypeName'),*it[1:])
        if h=='call':
            need(n>=1,'call needs a callee'); fn=x[1]; args=x[2:]
        else:
            need(h not in RESERVED_EXPR,f'wrong arity for reserved expression head {h}')
            fn=x[0]; args=x[1:]
        return c('Call',self.lower(fn,'Expr'),c('Arguments',cl(self.lower(a,'Expr') for a in args)) if args else c('NoArguments'))

    def strings(self,x: S) -> S:
        seq=x[1:] if hd(x)=='strings' else [x]
        out=[]
        for a in seq:
            if isinstance(a,Text): out.append(Text(c_string(a.data)))
            else:
                need(hd(a)=='lit' and len(a)==2,'strings needs contents strings or (lit exact-C-string-token)')
                out.append(byte_payload(a[1]))
        need(bool(out),'empty string token sequence')
        return cl(out)

    def base_atom(self,x: S,decl: bool) -> tuple[str,S]:
        if isinstance(x,Sym):
            if x in PRIMITIVES: return 'primitive',c(PRIMITIVES[x])
            if x in UNIQUE: return 'unique',c(UNIQUE[x])
            if x in QUALIFIERS:
                q=c(QUALIFIERS[x]); return 'other',c('QualifierSpecifier',q) if decl else q
            if decl and x in STORAGE: return 'other',c('StorageSpecifier',c(STORAGE[x]))
            if decl and x in FSPEC: return 'other',c('FunctionSpecifier',c(FSPEC[x]))
            return 'unique',c('TypedefType',ident(x))
        h=hd(x)
        if h in ('struct','union'):
            arity(x,1,2); kind=c('Struct' if h=='struct' else 'Union')
            if len(x)==2: return 'unique',c('StructType',c('StructReference',kind,ident(x[1])))
            need(isinstance(x[2],list),'record fields must be a list')
            name=c('NoIdent') if x[1]==sy('_') else c('SomeIdent',ident(x[1]))
            rec=c('StructDefinition',kind,name,cl(self.lower(a,'StructDeclaration') for a in x[2])) if x[2] else c('GNUEmptyStruct',kind,name)
            return 'unique',c('StructType',rec)
        if h=='enum':
            arity(x,1,2)
            if len(x)==2: return 'unique',c('EnumType',c('EnumReference',ident(x[1])))
            need(isinstance(x[2],list) and bool(x[2]),'enum requires a nonempty enumerator list')
            name=c('NoIdent') if x[1]==sy('_') else c('SomeIdent',ident(x[1]))
            return 'unique',c('EnumType',c('EnumDefinition',name,cl(self.lower(a,'Enumerator') for a in x[2])))
        if h=='_Atomic': arity(x,1); return 'unique',c('AtomicType',self.lower(x[1],'TypeName'))
        if h in ('typeof','typeof-type'):
            arity(x,1)
            return 'unique',c('GNUTypeofExpression' if h=='typeof' else 'GNUTypeofType',c('GNUTypeof'),self.lower(x[1],'Expr' if h=='typeof' else 'TypeName'))
        if h in ('alignas','alignas-type'):
            need(decl,'alignment specifier is not a type qualifier'); arity(x,1)
            return 'other',c('AlignmentSpecifier',c('AlignasExpression' if h=='alignas' else 'AlignasType',self.lower(x[1],'Expr' if h=='alignas' else 'TypeName')))
        if h=='id': return 'unique',c('TypedefType',ident(x))
        raise SurfaceError('unknown base type/specifier '+dumps(x))

    def specs(self,x: S,decl: bool) -> S:
        if hd(x).startswith('c:'): return self.lower(x,'DeclarationSpecifiers' if decl else 'SpecifierQualifiers')
        parts=[x] if not isinstance(x,list) or hd(x) in TYPE_HEADS|{'id','alignas','alignas-type'} else x
        need(bool(parts),'empty declaration specifier sequence')
        items=[self.base_atom(a,decl) for a in parts]
        def build(items):
            k,t=items[0]; rest=items[1:]
            if k=='other':
                need(bool(rest),'qualifier/storage without a type')
                return c('PrefixedDeclarationSpecifiers' if decl else 'PrefixedSpecifierQualifiers',t,build(rest))
            if k=='primitive':
                need(all(k!='unique' for k,_ in rest),'cannot mix a unique type with primitive type specifiers')
                tails=[c(('PrimitiveSpecifierTail' if decl else 'PrimitiveQualifierTail') if k=='primitive' else ('NonTypeSpecifierTail' if decl else 'QualifierTail'),v) for k,v in rest]
                return c('PrimitiveDeclarationSpecifiers' if decl else 'PrimitiveSpecifierQualifiers',t,cl(tails))
            need(all(k=='other' for k,_ in rest),'multiple distinct base types in a declaration')
            tail=cl(v for _,v in rest)
            return c('UniqueDeclarationSpecifiers',t,tail) if decl else c('UniqueSpecifierQualifiers',t,c('SomeTypeQualifiers',tail) if rest else c('NoTypeQualifiers'))
        return build(items)

    def quals(self,qs) -> S:
        return c('SomeTypeQualifiers',cl(self.lower(q,'TypeQualifier') for q in qs)) if qs else c('NoTypeQualifiers')

    def direct(self,d: S) -> S:
        return d[1] if isc(d,'Declarator') else c('ParenthesizedDeclarator',d)

    def typed(self,t: S,d: S,decl=True) -> tuple[S,S]:
        h=hd(t)
        if h=='*':
            need(len(t)>=2,'pointer type needs a pointee'); q=self.quals(t[2:])
            p=c('NestedPointer',q,d[1]) if isc(d,'PointerDeclarator') else c('Pointer',q)
            dd=d[2] if isc(d,'PointerDeclarator') else self.direct(d)
            return self.typed(t[1],c('PointerDeclarator',p,dd),decl)
        if h=='[]':
            need(len(t)>=3,'array type is ([] element bound qualifiers...)')
            bound=t[2]; qs=t[3:]; dd=self.direct(d)
            if bound==sy('*'): dd=c('VariableArrayDeclarator',dd,self.quals(qs))
            elif hd(bound)=='static':
                arity(bound,1); dd=c('StaticArrayDeclarator',dd,self.quals(qs),self.lower(bound[1],'Expr'))
            elif hd(bound)=='qstatic':
                arity(bound,1); need(bool(qs),'qstatic needs qualifiers')
                dd=c('QualifiedStaticArrayDeclarator',dd,cl(self.lower(q,'TypeQualifier') for q in qs),self.lower(bound[1],'Expr'))
            else: dd=c('ArrayDeclarator',dd,self.quals(qs),c('NoExpression') if bound==sy('_') else c('SomeExpression',self.lower(bound,'Expr')))
            return self.typed(t[1],c('Declarator',dd),decl)
        if h=='->':
            arity(t,2); ps=t[1]; dd=self.direct(d)
            if hd(ps)=='old':
                ids=ps[1:]
                dd=c('OldFunctionDeclarator',dd,c('Identifiers',cl(ident(a) for a in ids)) if ids else c('NoIdentifiers'))
            else: dd=c('FunctionDeclarator',dd,self.parameters(ps))
            return self.typed(t[2],c('Declarator',dd),decl)
        return self.specs(t,decl),d

    def erase(self,d: S) -> S:
        if isc(d,'Declarator'):
            if isc(d[1],'NameDeclarator'): return c('NoAbstractDeclarator')
            return c('SomeAbstractDeclarator',c('AbstractDeclarator',self.erase_direct(d[1])))
        need(isc(d,'PointerDeclarator'),'cannot erase this declarator')
        if isc(d[2],'NameDeclarator'): return c('SomeAbstractDeclarator',c('AbstractPointer',d[1]))
        return c('SomeAbstractDeclarator',c('PointerAbstractDeclarator',d[1],self.erase_direct(d[2])))

    def erase_direct(self,d: S) -> S:
        if isc(d,'ParenthesizedDeclarator'):
            a=self.erase(d[1]); need(isc(a,'SomeAbstractDeclarator'),'empty parenthesized abstract declarator')
            return c('ParenthesizedAbstractDeclarator',a[1])
        if isc(d,'FunctionDeclarator') or isc(d,'OldFunctionDeclarator'):
            ps=c('SomeParameterTypes',d[2]) if isc(d,'FunctionDeclarator') else c('NoParameterTypes')
            need(isc(d,'FunctionDeclarator') or isc(d[2],'NoIdentifiers'),'identifier lists are not abstract function types')
            return c('AbstractFunction',ps) if isc(d[1],'NameDeclarator') else c('NestedAbstractFunction',self.erase_direct(d[1]),ps)
        table={'c:ArrayDeclarator':'Array','c:StaticArrayDeclarator':'StaticArray','c:QualifiedStaticArrayDeclarator':'QualifiedStaticArray','c:VariableArrayDeclarator':'VariableArray'}
        need(hd(d) in table,'unsupported abstract declarator; use exact constructor')
        args=d[2:]
        if isc(d,'VariableArrayDeclarator'):
            need(isc(d[2],'NoTypeQualifiers'),'abstract [*] cannot have qualifiers in the C11 grammar'); args=[]
        return c('Abstract'+table[hd(d)],*args) if isc(d[1],'NameDeclarator') else c('NestedAbstract'+table[hd(d)],self.erase_direct(d[1]),*args)

    def parameter(self,p: S) -> S:
        name=None; t=p
        if isinstance(p,list) and len(p)==2 and hd(p) not in TYPE_HEADS|{'id'}:
            last=p[1]
            if (isinstance(last,Sym) and last not in KEYWORDS and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',last)) or hd(last)=='id':
                t,name=p
        if isinstance(t,list) and len(t)==1: t=t[0]
        specs,d=self.typed(t,c('Declarator',c('NameDeclarator',ident(name) if name is not None else sy('__compact_type_hole'))))
        return c('NamedParameter',specs,d) if name is not None else c('AbstractParameter',specs,self.erase(d))

    def parameters(self,ps: S) -> S:
        need(isinstance(ps,list),'parameter list must be a list')
        variadic=bool(ps) and ps[-1]==sy('...')
        items=ps[:-1] if variadic else ps
        if not items:
            need(not variadic,'C11 variadic prototype needs a named parameter before ...')
            items=[sy('void')]
        return c('VariadicParameterTypes' if variadic else 'ParameterTypes',cl(self.lower(a,'Parameter') for a in items))

    def init(self,x: S) -> S:
        h=hd(x)
        if h in ('init','init,'):
            if len(x)==1:
                need(h=='init','empty trailing-comma initializer is invalid'); return c('GNUEmptyInitializer')
            return c('InitializerListComma' if h=='init,' else 'InitializerList',cl(self.lower(a,'InitializerElement') for a in x[1:]))
        return c('InitializerExpression',self.lower(x,'Expr'))

    def variable(self,t: S,name: S,value: list[S]) -> tuple[S,S]:
        spec,d=self.typed(t,c('Declarator',c('NameDeclarator',ident(name))))
        return spec,c('InitDeclaratorValue',d,self.lower(value[0],'Initializer')) if value else c('InitDeclarator',d)

    def declaration(self,x: S) -> S:
        h=hd(x)
        if h=='static-assert': return c('StaticAssertionDeclaration',self.lower(x,'StaticAssertion'))
        if h in ('struct','union','enum'):
            need(len(x)>=2,'tag definition needs its name (_ means anonymous)')
            return c('Declaration',self.specs(sf(h,x[1],x[2:]),True),c('NoInitDeclarators'))
        if h=='typedef':
            arity(x,2)
            spec,d=self.typed(x[1],c('Declarator',c('NameDeclarator',ident(x[2]))))
            return c('Declaration',c('PrefixedDeclarationSpecifiers',c('StorageSpecifier',c('Typedef')),spec),c('InitDeclarators',cl([c('InitDeclarator',d)])))
        if h=='var':
            arity(x,1,3)
            if len(x)==2: return c('Declaration',self.specs(x[1],True),c('NoInitDeclarators'))
            s,d=self.variable(x[1],x[2],x[3:]); return c('Declaration',s,c('InitDeclarators',cl([d])))
        if h=='vars':
            need(len(x)>=2,'vars needs at least one variable tuple')
            entries=[]; common=None
            for a in x[1:]:
                need(isinstance(a,list) and len(a) in (2,3),'vars entries are (Type name [initializer])')
                s,d=self.variable(a[0],a[1],a[2:])
                need(common is None or s==common,'vars must have identical declaration specifiers: it preserves one C declaration group')
                common=s; entries.append(d)
            return c('Declaration',common,c('InitDeclarators',cl(entries)))
        raise SurfaceError('expected var, vars, typedef, tag definition or static-assert, got '+dumps(x))

    def field(self,x: S) -> S:
        if hd(x)=='static-assert': return c('StructStaticAssertion',self.lower(x,'StaticAssertion'))
        need(isinstance(x,list) and len(x) in (1,2,4),'field is (Type name), (Type name : width), or (Type) for anonymous member')
        if len(x)==1: return c('StructDeclaration',self.specs(x[0],False),c('NoStructDeclarators'))
        name=x[1]; spec,d=self.typed(x[0],c('Declarator',c('NameDeclarator',sy('__compact_type_hole') if name==sy('_') else ident(name))),False)
        if len(x)==4:
            need(x[2]==sy(':'),'bitfield requires : before width')
            sd=c('BitField',c('NoDeclarator') if name==sy('_') else c('SomeDeclarator',d),self.lower(x[3],'Expr'))
        else:
            need(name!=sy('_'),'_ is only an unnamed bitfield here'); sd=c('StructDeclarator',d)
        return c('StructDeclaration',spec,c('StructDeclarators',cl([sd])))

    def statement(self,x: S) -> S:
        h=hd(x)
        def wrap(t): return c(STMT_SORT[hd(t)[2:]],t)
        if h=='block': return c('CompoundStatement',self.lower(x,'Block'))
        if h=='empty': arity(x,0); return c('ExpressionStatement',c('NoFullExpression'))
        if h=='if':
            arity(x,2,3); return wrap(c('IfElse' if len(x)==4 else 'If',self.lower(x[1],'Expr'),*(self.lower(a,'Statement') for a in x[2:])))
        if h in ('switch','while'):
            arity(x,2); return wrap(c('Switch' if h=='switch' else 'While',self.lower(x[1],'Expr'),self.lower(x[2],'Statement')))
        if h=='do': arity(x,2); return wrap(c('DoWhile',self.lower(x[1],'Statement'),self.lower(x[2],'Expr')))
        if h=='for':
            arity(x,4); decl=hd(x[1]) in DECL_HEADS or (hd(x[1]).startswith('c:') and self.constructors.get(hd(x[1])[2:],(None,))[0]=='Declaration')
            return wrap(c('ForDeclaration' if decl else 'For',self.lower(x[1],'Declaration' if decl else 'OptionalFullExpression'),self.lower(x[2],'OptionalFullExpression'),self.lower(x[3],'OptionalFullExpression'),self.lower(x[4],'Statement')))
        if h=='return': arity(x,0,1); return wrap(c('Return',self.lower(x[1] if len(x)==2 else [],'OptionalFullExpression')))
        if h in ('break','continue'): arity(x,0); return wrap(c('Break' if h=='break' else 'Continue'))
        if h in ('goto','goto*'): arity(x,1); return wrap(c('Goto' if h=='goto' else 'GNUIndirectGoto',self.lower(x[1],'Name' if h=='goto' else 'Expr')))
        if h in ('label','case'): arity(x,2); return wrap(c('Label' if h=='label' else 'Case',self.lower(x[1],'Name' if h=='label' else 'Expr'),self.lower(x[2],'Statement')))
        if h=='default': arity(x,1); return wrap(c('Default',self.lower(x[1],'Statement')))
        if h=='case-range': arity(x,3); return wrap(c('GNUCaseRange',self.lower(x[1],'Expr'),self.lower(x[2],'Expr'),self.lower(x[3],'Statement')))
        return c('ExpressionStatement',c('SomeFullExpression',self.lower(x,'Expr')))

    def function(self,x: S) -> S:
        h=hd(x); need(h in ('fn','fn-old'),'expected fn or fn-old')
        need(len(x)>=4,'fn requires return specifier/type, name, parameter list, and zero or more body forms')
        r,name,ps=x[1:4]
        if h=='fn-old':
            need(len(x)>=5 and isinstance(ps,list) and isinstance(x[4],list),'fn-old requires names then a declaration list')
            ps=sf('old',*ps); decls=cl(self.lower(a,'Declaration') for a in x[4]); body=x[5:]
        else: decls=cl(); body=x[4:]
        specs,d=self.typed(sf('->',ps,r),c('Declarator',c('NameDeclarator',ident(name))))
        return c('FunctionDefinition',specs,d,decls,self.lower(sf('block',*body),'Block'))

    # Below: inverse views. Every proposed contraction is checked by expansion.
    # A failed inverse never normalizes a tree: the local constructor is retained.
    def compact(self,t: S,s: str) -> S:
        s=self.rep(s); shape=self.shapes[s]
        if shape['kind']=='token': return surface_name(t) if shape['representation']=='identifier' else surface_bytes(t.data)
        if shape['kind']=='list': return [self.compact(a,shape['element']) for a in t[1:]]
        try:
            candidate=self.candidate(t,s)
            if candidate is not None and self.lower(candidate,s)==t: return candidate
        except (SurfaceError,IndexError,KeyError,TypeError,UnicodeError):
            pass
        label=hd(t)[2:]; result,fields=self.constructors[label]
        need(result==s,'ill-sorted AST during compaction')
        return c(label,*(self.compact(a,k) for a,k in zip(t[1:],fields)))

    def compact_num(self,b: bytes) -> S:
        s=b.decode('ascii')
        # Bare small decimals and explicit integer suffixes survive the
        # supplied MeTTa reader without losing source spelling. Others get #.
        if re.fullmatch(r'(?:0|[1-9][0-9]*)',s) and int(s)<=2147483647: return sy(s)
        if re.fullmatch(r'(?:0|[1-9][0-9]*)[uUlL]+',s): return sy(s)
        return sy('#'+s)

    def compact_string(self,q: Text) -> S:
        b=q.data
        if b.startswith(b'"') and b.endswith(b'"'):
            try:
                form=parse(b)
                if len(form)==1 and isinstance(form[0],Text) and c_string(form[0].data)==b: return form[0]
            except SurfaceError: pass
        return sf('lit',surface_bytes(q.data))

    def unstrings(self,t: S) -> S:
        xs=[self.compact_string(a) for a in t[1:]]
        return xs[0] if len(xs)==1 else sf('strings',*xs)

    def unbase(self,t: S) -> S:
        label=hd(t)[2:]
        maps={**PRIMITIVES,**UNIQUE,**QUALIFIERS,**STORAGE,**FSPEC}
        rev={v:k for k,v in maps.items()}
        if label in rev: return sy(rev[label])
        if label in ('StorageSpecifier','QualifierSpecifier','FunctionSpecifier','AlignmentSpecifier',
                     'PrimitiveSpecifierTail','NonTypeSpecifierTail','PrimitiveQualifierTail','QualifierTail'):
            return self.unbase(t[1])
        if label=='TypedefType': return surface_name(t[1])
        if label=='AtomicType': return sf('_Atomic',self.compact(t[1],'TypeName'))
        if label=='StructType':
            z=t[1]; kind={'c:Struct':'struct','c:Union':'union'}[hd(z[1])]
            if isc(z,'StructReference'): return sf(kind,surface_name(z[2]))
            name=sy('_') if isc(z[2],'NoIdent') else surface_name(z[2][1])
            if isc(z,'GNUEmptyStruct'): return sf(kind,name,[])
            need(isc(z,'StructDefinition'),'unknown record form')
            return sf(kind,name,[self.compact(a,'StructDeclaration') for a in z[3][1:]])
        if label=='EnumType':
            z=t[1]
            if isc(z,'EnumReference'): return sf('enum',surface_name(z[1]))
            need(isc(z,'EnumDefinition'),'trailing enum comma keeps exact constructor')
            name=sy('_') if isc(z[1],'NoIdent') else surface_name(z[1][1])
            return sf('enum',name,[self.compact(a,'Enumerator') for a in z[2][1:]])
        if label in ('AlignasType','AlignasExpression'):
            return sf('alignas-type' if label=='AlignasType' else 'alignas',self.compact(t[1],'TypeName' if label=='AlignasType' else 'Expr'))
        if label in ('GNUTypeofType','GNUTypeofExpression') and isc(t[1],'GNUTypeof'):
            return sf('typeof-type' if label=='GNUTypeofType' else 'typeof',self.compact(t[2],'TypeName' if label=='GNUTypeofType' else 'Expr'))
        raise SurfaceError('no inverse for base specifier '+label)

    def unspecs(self,t: S) -> S:
        label=hd(t)[2:]
        if label in ('PrefixedDeclarationSpecifiers','PrefixedSpecifierQualifiers'):
            out=[self.unbase(t[1]),*self.unspecseq(t[2])]
        elif label in ('PrimitiveDeclarationSpecifiers','UniqueDeclarationSpecifiers','PrimitiveSpecifierQualifiers'):
            out=[self.unbase(t[1]),*(self.unbase(a) for a in t[2][1:])]
        elif label=='UniqueSpecifierQualifiers':
            out=[self.unbase(t[1]),*self.unquals(t[2])]
        else: raise SurfaceError('unknown specifier sequence')
        return out[0] if len(out)==1 else out

    def unspecseq(self,t: S) -> list[S]:
        v=self.unspecs(t)
        return [v] if not isinstance(v,list) or hd(v) in TYPE_HEADS|{'id','alignas','alignas-type'} else v

    def unquals(self,q: S) -> list[S]:
        return [] if isc(q,'NoTypeQualifiers') else [self.unbase(a) for a in q[1][1:]]

    def unpointer(self,p: S,b: S) -> S:
        b=sf('*',b,*self.unquals(p[1]))
        return self.unpointer(p[2],b) if isc(p,'NestedPointer') else b

    def untyped(self,d: S,b: S) -> tuple[S,S]:
        if isc(d,'PointerDeclarator'): return self.undirect(d[2],self.unpointer(d[1],b))
        need(isc(d,'Declarator'),'unsupported decorated declarator')
        return self.undirect(d[1],b)

    def undirect(self,d: S,b: S) -> tuple[S,S]:
        if isc(d,'NameDeclarator'): return b,surface_name(d[1])
        if isc(d,'ParenthesizedDeclarator'): return self.untyped(d[1],b)
        if isc(d,'FunctionDeclarator'): return self.undirect(d[1],sf('->',self.unparams(d[2]),b))
        if isc(d,'OldFunctionDeclarator'):
            ids=[] if isc(d[2],'NoIdentifiers') else [surface_name(a) for a in d[2][1][1:]]
            return self.undirect(d[1],sf('->',sf('old',*ids),b))
        h=hd(d)
        if h in ('c:ArrayDeclarator','c:StaticArrayDeclarator','c:QualifiedStaticArrayDeclarator','c:VariableArrayDeclarator'):
            if h=='c:ArrayDeclarator': bound=sy('_') if isc(d[3],'NoExpression') else self.compact(d[3][1],'Expr'); qs=self.unquals(d[2])
            elif h=='c:VariableArrayDeclarator': bound=sy('*'); qs=self.unquals(d[2])
            elif h=='c:StaticArrayDeclarator': bound=sf('static',self.compact(d[3],'Expr')); qs=self.unquals(d[2])
            else: bound=sf('qstatic',self.compact(d[3],'Expr')); qs=[self.unbase(a) for a in d[2][1:]]
            return self.undirect(d[1],sf('[]',b,bound,*qs))
        raise SurfaceError('decorated declarator retained exactly')

    def unerase(self,o: S) -> S:
        name=c('NameDeclarator',sy('__compact_type_hole'))
        if isc(o,'NoAbstractDeclarator'): return c('Declarator',name)
        a=o[1]
        if isc(a,'AbstractPointer'): return c('PointerDeclarator',a[1],name)
        if isc(a,'AbstractDeclarator'): return c('Declarator',self.unerase_direct(a[1]))
        if isc(a,'PointerAbstractDeclarator'): return c('PointerDeclarator',a[1],self.unerase_direct(a[2]))
        raise SurfaceError('unknown abstract declarator')

    def unerase_direct(self,a: S) -> S:
        h=hd(a)[2:]; base=c('NameDeclarator',sy('__compact_type_hole'))
        if h=='ParenthesizedAbstractDeclarator': return c('ParenthesizedDeclarator',self.unerase(c('SomeAbstractDeclarator',a[1])))
        nested=h.startswith('Nested'); suffix=h[len('NestedAbstract'):] if nested else h[len('Abstract'):]
        rest=a[1:]
        if nested: base=self.unerase_direct(rest[0]); rest=rest[1:]
        if suffix=='Function': return c('OldFunctionDeclarator',base,c('NoIdentifiers')) if isc(rest[0],'NoParameterTypes') else c('FunctionDeclarator',base,rest[0][1])
        if suffix=='VariableArray': return c('VariableArrayDeclarator',base,c('NoTypeQualifiers'))
        need(suffix in ('Array','StaticArray','QualifiedStaticArray'),'unknown abstract postfix')
        return c(suffix+'Declarator',base,*rest)

    def unparam(self,t: S) -> S:
        b=self.unspecs(t[1])
        if isc(t,'NamedParameter'):
            typ,name=self.untyped(t[2],b); return [typ,name]
        need(isc(t,'AbstractParameter'),'unknown parameter')
        typ,_=self.untyped(self.unerase(t[2]),b); return typ

    def unparams(self,t: S) -> list[S]:
        ps=[self.compact(a,'Parameter') for a in t[1][1:]]
        if isc(t,'ParameterTypes') and ps==[sy('void')]: return []
        if isc(t,'VariadicParameterTypes'): ps.append(sy('...'))
        return ps

    def candidate(self,t: S,s: str) -> S | None:
        h=hd(t)[2:]
        cp=lambda a,k: self.compact(a,k)
        if s=='Expr':
            if h=='Var': return surface_name(t[1])
            if h in ('IntConst','FloatConst'): return self.compact_num(t[1].data)
            if h=='CharConst': return sf('lit',surface_bytes(t[1].data))
            if h=='StringLiterals': return self.unstrings(t[1])
            revu={v:k for k,v in UN.items()}; revb={v:k for k,v in BIN.items()}
            if h in revu: return sf(revu[h],cp(t[1],'Expr'))
            if h in revb: return sf(revb[h],cp(t[1],'Expr'),cp(t[2],'Expr'))
            for name,(tag,sorts) in EXTRA_EXPR.items():
                if tag==h: return sf(name,*(cp(a,k) for a,k in zip(t[1:],sorts)))
            if h=='Call':
                f=cp(t[1],'Expr'); args=[] if isc(t[2],'NoArguments') else [cp(a,'Expr') for a in t[2][1][1:]]
                return sf('call',f,*args) if isinstance(f,Sym) and f in RESERVED_EXPR else [f,*args]
            if h=='Generic': return sf('generic',cp(t[1],'Expr'),*(cp(a,'GenericAssociation') for a in t[2][1:]))
            if h=='GNUStatementExpression': return sf('stmt-expr',*(cp(a,'BlockItem') for a in t[1][1][1:]))
            if h in ('CompoundLiteral','CompoundLiteralComma','GNUEmptyCompoundLiteral'):
                ini=c({'CompoundLiteral':'InitializerList','CompoundLiteralComma':'InitializerListComma','GNUEmptyCompoundLiteral':'GNUEmptyInitializer'}[h],*t[2:])
                return sf('compound',cp(t[1],'TypeName'),cp(ini,'Initializer'))
        if s in ('DeclarationSpecifiers','SpecifierQualifiers'): return self.unspecs(t)
        if s=='TypeQualifier': return self.unbase(t)
        if s=='TypeName': return self.untyped(self.unerase(t[2]),self.unspecs(t[1]))[0]
        if s=='Parameter': return self.unparam(t)
        if s=='ParameterTypeList': return self.unparams(t)
        if s=='Initializer':
            if h=='InitializerExpression': return cp(t[1],'Expr')
            if h in ('InitializerList','InitializerListComma'): return sf('init,' if h.endswith('Comma') else 'init',*(cp(a,'InitializerElement') for a in t[1][1:]))
            if h=='GNUEmptyInitializer': return sf('init')
        if s=='InitializerElement':
            if h=='InitializerElement': return cp(t[1],'Initializer')
            if h=='DesignatedInitializerElement': return sf('at',[cp(a,'Designator') for a in t[1][1:]],cp(t[2],'Initializer'))
        if s=='Designator':
            if h=='MemberDesignator': return sf('.',cp(t[1],'Name'))
            if h=='ArrayDesignator': return sf('[]',cp(t[1],'Expr'))
        if s=='GenericAssociation':
            if h=='GenericType': return [cp(t[1],'TypeName'),cp(t[2],'Expr')]
            if h=='GenericDefault': return [sy('default'),cp(t[1],'Expr')]
        if s=='Enumerator': return cp(t[1],'Name') if h=='Enumerator' else [cp(t[1],'Name'),cp(t[2],'Expr')]
        if s=='StaticAssertion': return sf('static-assert',cp(t[1],'Expr'),self.unstrings(t[2]))
        if s=='Declaration':
            if h=='StaticAssertionDeclaration': return cp(t[1],'StaticAssertion')
            if h!='Declaration': return None
            b=self.unspecs(t[1])
            if isc(t[2],'NoInitDeclarators'):
                if hd(b) in ('struct','union','enum') and len(b)==3: return sf(hd(b),b[1],*b[2])
                return sf('var',b)
            entries=[]
            for a in t[2][1][1:]:
                typ,name=self.untyped(a[1],b)
                entries.append([typ,name,*([cp(a[2],'Initializer')] if isc(a,'InitDeclaratorValue') else [])])
            return sf('var',*entries[0]) if len(entries)==1 else sf('vars',*entries)
        if s=='StructDeclaration':
            if h=='StructStaticAssertion': return cp(t[1],'StaticAssertion')
            if h!='StructDeclaration': return None
            b=self.unspecs(t[1])
            if isc(t[2],'NoStructDeclarators'): return [b]
            ds=t[2][1][1:]; need(len(ds)==1,'grouped fields keep exact constructor')
            d=ds[0]
            if isc(d,'BitField'):
                typ,name=(b,sy('_')) if isc(d[1],'NoDeclarator') else self.untyped(d[1][1],b)
                return [typ,name,sy(':'),cp(d[2],'Expr')]
            typ,name=self.untyped(d[1],b); return [typ,name]
        if s in ('OptionalFullExpression','OptionalExpression'):
            return [] if h in ('NoFullExpression','NoExpression') else cp(t[1],'Expr')
        if s=='Block': return sf('block',*(cp(a,'BlockItem') for a in t[1][1:]))
        if s=='BlockItem':
            if h in ('BlockStatement','BlockDeclaration'): return cp(t[1],'Statement' if h=='BlockStatement' else 'Declaration')
        if s=='Statement':
            if h=='CompoundStatement': return cp(t[1],'Block')
            if h in ('SelectionStatement','IterationStatement','JumpStatement','LabeledStatement'): return cp(t[1],h)
            if h=='ExpressionStatement': return sf('empty') if isc(t[1],'NoFullExpression') else cp(t[1][1],'Expr')
        if s in {'SelectionStatement','IterationStatement','JumpStatement','LabeledStatement'}:
            if h in ('If','IfElse'): return sf('if',cp(t[1],'Expr'),*(cp(a,'Statement') for a in t[2:]))
            if h in ('Switch','While'): return sf(h.lower(),cp(t[1],'Expr'),cp(t[2],'Statement'))
            if h=='DoWhile': return sf('do',cp(t[1],'Statement'),cp(t[2],'Expr'))
            if h in ('For','ForDeclaration'): return sf('for',cp(t[1],'Declaration' if h=='ForDeclaration' else 'OptionalFullExpression'),cp(t[2],'OptionalFullExpression'),cp(t[3],'OptionalFullExpression'),cp(t[4],'Statement'))
            if h=='Return': return sf('return',*([] if isc(t[1],'NoFullExpression') else [cp(t[1][1],'Expr')]))
            if h in ('Break','Continue'): return sf(h.lower())
            if h in ('Goto','GNUIndirectGoto'): return sf('goto' if h=='Goto' else 'goto*',cp(t[1],'Name' if h=='Goto' else 'Expr'))
            if h in ('Label','Case'): return sf(h.lower(),cp(t[1],'Name' if h=='Label' else 'Expr'),cp(t[2],'Statement'))
            if h=='Default': return sf('default',cp(t[1],'Statement'))
            if h=='GNUCaseRange': return sf('case-range',cp(t[1],'Expr'),cp(t[2],'Expr'),cp(t[3],'Statement'))
        if s=='FunctionDefinition' and h=='FunctionDefinition':
            typ,name=self.untyped(t[2],self.unspecs(t[1])); need(hd(typ)=='->','function definition needs function declarator')
            ps,r=typ[1:]; body=[cp(a,'BlockItem') for a in t[4][1][1:]]
            if hd(ps)=='old': return sf('fn-old',r,name,ps[1:],[cp(a,'Declaration') for a in t[3][1:]],*body)
            need(t[3]==cl(),'prototype function with old-style parameter declarations keeps exact form')
            return sf('fn',r,name,ps,*body)
        if s=='ExternalDeclaration':
            if h=='ExternalFunction': return cp(t[1],'FunctionDefinition')
            if h=='ExternalDeclaration': return cp(t[1],'Declaration')
        if s=='TranslationUnit' and h=='TranslationUnit': return sf('unit',*(cp(a,'ExternalDeclaration') for a in t[1][1:]))
        return None


def one_tree(data: bytes) -> S:
    fs=parse(data); need(len(fs)==1,'expected exactly one tree'); return fs[0]

def load_codec(schema: Path | None=None) -> Codec:
    return Codec(json.loads((schema or Path(__file__).parent/'generated/CGNU.schema.json').read_text()))

def checked_c(ast: S, cesta: Path, std='gnu11') -> bytes:
    """Validate with native Cesta; reject output that parses to a different tree."""
    with tempfile.TemporaryDirectory(prefix='compact-cesta-') as td:
        p=Path(td)/'program.csexp'; p.write_text(dumps(ast)+'\n')
        run=subprocess.run([str(cesta),'write','--std='+std,str(p)],capture_output=True,timeout=90)
        need(run.returncode==0,'Cesta refused tree: '+run.stderr.decode(errors='replace'))
        q=Path(td)/'program.c'; q.write_bytes(run.stdout)
        back=subprocess.run([str(cesta),'read','--std='+std,str(q)],capture_output=True,timeout=90)
        need(back.returncode==0,'Cesta refused printed C: '+back.stderr.decode(errors='replace'))
        need(one_tree(back.stdout)==ast,'printed C reparses differently; explicit block/grouping or exact constructor is required')
        return run.stdout

def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['lower','compact','emit','grammar'])
    ap.add_argument('file',nargs='?',type=Path)
    ap.add_argument('--schema',type=Path)
    ap.add_argument('--cesta',type=Path,help='native Cesta binary (required by emit)')
    ap.add_argument('--std',default='gnu11',choices=['c11','gnu11'])
    ap.add_argument('--sort',default='TranslationUnit')
    ap.add_argument('--width',type=int,default=100)
    args=ap.parse_args()
    try:
        codec=load_codec(args.schema)
        if args.command=='grammar':
            for sort,shape in codec.shapes.items():
                print(f'\n{sort} ::=')
                if shape['kind']=='token':
                    print('    '+('CIdentifier | "(" "id" BytePayload ")" | "(" "c:Symbol" BytePayload ")"' if shape['representation']=='identifier' else 'BytePayload'))
                elif shape['kind']=='list':
                    print(f'    "(" {shape["element"]}* ")" | "(" "c:List" {shape["element"]}* ")"')
                else:
                    rules=[]
                    for label,(result,fields) in codec.constructors.items():
                        if result==sort: rules.append('"(" "c:'+label+'" '+ ' '.join(fields)+' ")"')
                    print('    '+'\n  | '.join(rules))
            return 0
        data=args.file.read_bytes() if args.file else sys.stdin.buffer.read()
        if args.command=='compact':
            t=one_tree(data); compact=codec.compact(t,args.sort)
            need(codec.lower(compact,args.sort)==t,'internal compact round-trip failure')
            print(pretty(compact,width=args.width)); return 0
        forms=parse(data)
        surface=forms[0] if len(forms)==1 else sf('unit',*forms)
        if args.sort=='TranslationUnit' and hd(surface) not in ('unit','c:TranslationUnit','c:GNUEmptyTranslationUnit'):
            surface=sf('unit',*forms)
        t=codec.lower(surface,args.sort)
        if args.command=='emit':
            need(args.cesta is not None,'emit requires --cesta /path/to/build/cesta')
            sys.stdout.buffer.write(checked_c(t,args.cesta.resolve(),args.std))
        else: print(dumps(t))
        return 0
    except (SurfaceError,ValueError,OSError,RecursionError,subprocess.SubprocessError) as e:
        print('compact-cesta: '+str(e),file=sys.stderr); return 1

if __name__=='__main__':
    raise SystemExit(main())
