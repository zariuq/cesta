#define _POSIX_C_SOURCE 200809L
#include "macro.h"
#include "sema.h"

#include <stdlib.h>
#include <string.h>

CeText ce_builtin_TheoryModuleSyntax(void);

enum { ARG_ONCE, ARG_DEFERRED, ARG_REPEAT, ARG_SYNTAX };
enum { CATEGORY_VALUE, CATEGORY_ADDRESSABLE, CATEGORY_MODIFIABLE, CATEGORY_SYNTAX };
typedef struct {
    CeText name;
    int sort;
    CeAtom *type;
    unsigned evaluation, category;
    size_t repetitions, uses;
} MacroArgument;
typedef struct { CeText name, space; } MacroLocal;
typedef struct {
    CeText key, name, header, type_name, alias;
    CeAtom *type;
    bool used, intrinsic;
} MacroPort;
typedef struct {
    CeText label, head, path, theory;
    bool gnu, type_parameters;
    int result;
    MacroArgument *arguments;
    size_t arity;
    MacroLocal *locals;
    size_t nlocals;
    MacroPort *ports;
    size_t nports;
    CeAtom *type, *body, *contract;
    CeText *helpers;
    size_t nhelpers;
} MacroRule;
struct CeMacros {
    CeArena arena;
    CeGrammar syntax;
    CeCatalog catalog;
    const CeGrammar *grammar;
    CeViewProfile view;
    MacroRule *rules;
    size_t len;
    CeText *reserved;
    size_t nreserved, serial;
    const CeTree **safe;
    size_t nsafe;
    const CeAtom **private_atoms;
    CeTree **private_trees;
    size_t nprivate;
    CeText *guards;
    size_t nguards, expanding;
    CeText unpack, diagnostic_begin, diagnostic_end;
    bool prelude_emitted;
    bool unguarded;
};
static bool definition_shape(CeMacros *macros, MacroRule *rule, CeError *error);
static void inventory(CeMacros *macros, const CeAtom *atom);
static CeText fresh(CeMacros *macros);

static bool form(const CeAtom *a, const char *key) {
    return a->kind == CE_ATOM_LIST && a->len && a->items[0]->kind == CE_ATOM_SYMBOL &&
        ce_is(a->items[0]->text,key);
}
static CeAtom *symbol(CeArena *a, CeText text, size_t offset) {
    CeAtom *atom = ce_atom_new(a,CE_ATOM_SYMBOL,ce_text(a,text.data,text.len),0);
    atom->offset = offset; return atom;
}
static CeAtom *word(CeArena *a, const char *name) { return symbol(a,ce_cstr(a,name),0); }
static CeAtom *list(CeArena *a, const char *head, size_t n) {
    CeAtom *atom = ce_atom_new(a,CE_ATOM_LIST,(CeText){0},n+1);
    atom->items[0] = word(a,head); return atom;
}
static int named(const CeGrammar *g, CeText name) {
    for (size_t i = 0; i < g->nsymbols; ++i)
        if (g->symbols[i].kind != CE_LITERAL && ce_equal(g->symbols[i].name,name)) return (int)i;
    return -1;
}
static bool same(const CeGrammar *g, int a, int b) {
    return a >= 0 && b >= 0 && g->symbols[a].sort == g->symbols[b].sort;
}
static bool identifier(CeText name) {
    if (!name.len) return false;
    for (size_t i = 0; i < name.len; ++i) {
        unsigned c = name.data[i];
        if (!(c == '_' || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (i && c >= '0' && c <= '9') || c >= 128)) return false;
    }
    return true;
}
static bool space(CeText name) {
    return ce_is(name,"ordinary") || ce_is(name,"tag") || ce_is(name,"member") || ce_is(name,"label");
}

/* The existing module parser owns syntax and composition. Quoted heads add
   atom constructors such as "+" without embedding a second textual parser. */
static CeAtom *source_atom(CeMacros *m, const CeTree *t, CeError *error) {
    const CeGrammar *g = &m->syntax; CeAtom *out;
    if (ce_tree_label(g,t,"ASTVar")) {
        FILE *file = tmpfile();
        if (!file) { ce_error(error,t->offset,"cannot render module name"); return NULL; }
        bool ok = ce_tree_print(g,t->children[0],t->children[0]->symbol,file,error);
        long length = ftell(file); CeText text = {0};
        if (ok && length >= 0) {
            text = (CeText){ce_alloc(&m->arena,(size_t)length+1),(size_t)length};
            rewind(file); ok = fread(text.data,1,text.len,file) == text.len;
            /* Dotted paths print token separators; names are structural data. */
            size_t n = 0;
            for (size_t i = 0; i < text.len; ++i)
                if (text.data[i] != ' ' && text.data[i] != '\n') text.data[n++] = text.data[i];
            text.len = n; text.data[n] = 0;
        } else ok = false;
        fclose(file); if (!ok) { ce_error(error,t->offset,"cannot read module name"); return NULL; }
        return symbol(&m->arena,text,t->offset);
    }
    if (ce_tree_label(g,t,"ASTText")) {
        CeText text;
        if (!ce_token_value(&m->arena,t->children[0]->spelling,&text,error)) return NULL;
        out = ce_atom_new(&m->arena,CE_ATOM_TEXT,text,0);
    } else if (ce_tree_label(g,t,"ASTInteger")) {
        out = symbol(&m->arena,t->children[0]->spelling,t->offset);
    } else if (ce_tree_label(g,t,"ASTSExp") || ce_tree_label(g,t,"ASTQuotedSExp")) {
        const CeTree *children = t->children[1]; CeText head;
        if (ce_tree_label(g,t,"ASTQuotedSExp")) {
            if (!ce_token_value(&m->arena,t->children[0]->spelling,&head,error)) return NULL;
        } else {
            const CeTree *label = t->children[0];
            if (label->len != 1 || label->children[0]->production != -1) {
                ce_error(error,t->offset,"macro data needs a named atom constructor"); return NULL;
            }
            head = label->children[0]->spelling;
        }
        out = ce_atom_new(&m->arena,CE_ATOM_LIST,(CeText){0},children->len+1);
        out->items[0] = symbol(&m->arena,head,t->offset);
        for (size_t i = 0; i < children->len; ++i) {
            out->items[i+1] = source_atom(m,children->children[i],error);
            if (!out->items[i+1]) return NULL;
        }
    } else {
        ce_error(error,t->offset,"macro data cannot contain implicit substitution or freshness syntax"); return NULL;
    }
    out->offset = t->offset; return out;
}
static bool field(const CeAtom *atom, const char *name, size_t count, CeError *error) {
    return (form(atom,name) && atom->len == count+1) ||
        ce_error(error,atom->offset,"macro contract requires (%s ...) with %zu fields",name,count);
}
static bool text(const CeAtom *a, CeText *out, CeError *error) {
    if (a->kind != CE_ATOM_TEXT) return ce_error(error,a->offset,"contract word requires a string");
    *out = a->text; return true;
}
static bool variable(const CeAtom *a, CeText *out, CeError *error) {
    if (a->kind != CE_ATOM_SYMBOL || !identifier(a->text))
        return ce_error(error,a->offset,"contract variable requires an identifier");
    *out = a->text; return true;
}
static bool natural(const CeAtom *a, size_t *out, CeError *error) {
    if (a->kind != CE_ATOM_SYMBOL || !a->text.len) return ce_error(error,a->offset,"repeat count requires an integer");
    size_t n = 0;
    for (size_t i = 0; i < a->text.len; ++i) {
        unsigned c = a->text.data[i];
        if (c < '0' || c > '9' || n > (SIZE_MAX-(c-'0'))/10)
            return ce_error(error,a->offset,"invalid repeat count");
        n = n*10+c-'0';
    }
    *out = n; return true;
}
static bool ctype(const CeAtom *a, CeError *error) {
    return (form(a,"CType") && (a->len == 2 || a->len == 3)) ||
        ce_error(error,a->offset,"CType requires specifiers and an optional TypeSlot declarator");
}
static MacroArgument *argument(MacroRule *r, CeText key) {
    for (size_t i = 0; i < r->arity; ++i) if (ce_equal(r->arguments[i].name,key)) return &r->arguments[i];
    return NULL;
}
static size_t local(MacroRule *r, CeText ns, CeText key) {
    for (size_t i = 0; i < r->nlocals; ++i)
        if (ce_equal(r->locals[i].space,ns) && ce_equal(r->locals[i].name,key)) return i;
    return SIZE_MAX;
}
static size_t port(MacroRule *r, CeText key) {
    for (size_t i = 0; i < r->nports; ++i) if (ce_equal(r->ports[i].key,key)) return i;
    return SIZE_MAX;
}
static bool type_parameter(MacroRule *r, const CeAtom *a, const CeGrammar *g, CeError *error) {
    CeText key;
    if (!r->type_parameters || !field(a,"TypeArgument",1,error) ||
        !variable(a->items[1],&key,error))
        return ce_error(error,a->offset,"TypeArgument requires a version 3 type parameter");
    MacroArgument *arg = argument(r,key);
    if (!arg || arg->category != CATEGORY_SYNTAX ||
        !ce_is(g->symbols[g->symbols[arg->sort].sort].name,"DeclarationSpecifiers"))
        return ce_error(error,a->offset,"TypeArgument must name a DeclarationSpecifiers syntax argument");
    return true;
}
static bool type_parameters_check(MacroRule *r, const CeAtom *a, const CeGrammar *g,
                                   bool allowed, CeError *error) {
    if (form(a,"TypeArgument"))
        return allowed ? type_parameter(r,a,g,error) :
            ce_error(error,a->offset,"definition-site ports cannot depend on use-site type arguments");
    if (form(a,"Argument") || form(a,"LocalRef") || form(a,"DefinitionRef") || form(a,"Capture"))
        return ce_error(error,a->offset,"CType parameters require TypeArgument");
    for (size_t i = 0; i < a->len; ++i)
        if (!type_parameters_check(r,a->items[i],g,allowed,error)) return false;
    return true;
}
static bool template_check(MacroRule *r, const CeAtom *a, CeError *error) {
    if (form(a,"Argument")) {
        CeText key;
        if (!field(a,"Argument",1,error) || !variable(a->items[1],&key,error)) return false;
        MacroArgument *arg = argument(r,key);
        if (!arg) return ce_error(error,a->offset,"unknown macro argument");
        ++arg->uses; return true;
    }
    if (form(a,"LocalRef")) {
        CeText ns, key;
        if (!field(a,"LocalRef",2,error) || !text(a->items[1],&ns,error) ||
            !variable(a->items[2],&key,error)) return false;
        return local(r,ns,key) != SIZE_MAX || ce_error(error,a->offset,"LocalRef has no binder in that namespace");
    }
    if (form(a,"Capture")) {
        CeText ns, key;
        return field(a,"Capture",2,error) && text(a->items[1],&ns,error) &&
            text(a->items[2],&key,error) && ((space(ns) && identifier(key)) ||
            ce_error(error,a->offset,"capture requires a C namespace and identifier"));
    }
    if (form(a,"DefinitionRef")) {
        CeText ns, key;
        if (!field(a,"DefinitionRef",2,error) || !text(a->items[1],&ns,error) ||
            !variable(a->items[2],&key,error)) return false;
        size_t i = port(r,key);
        return (ce_is(ns,"ordinary") && i != SIZE_MAX && !r->ports[i].intrinsic) ||
            ce_error(error,a->offset,"DefinitionRef has no native symbol port in that namespace");
    }
    if (form(a,"CompilerRef")) {
        CeText key;
        if (!field(a,"CompilerRef",1,error) || !variable(a->items[1],&key,error)) return false;
        size_t i = port(r,key);
        return (i != SIZE_MAX && r->ports[i].intrinsic) ||
            ce_error(error,a->offset,"CompilerRef has no declared compiler builtin");
    }
    if (form(a,"native") || form(a,"include") || form(a,"define") || form(a,"use-macros") ||
        form(a,"cpp-if") || form(a,"cpp-ifdef") || form(a,"cpp-ifndef"))
        return ce_error(error,a->offset,"macro template cannot hide binding or evaluation in opaque source");
    if (a->kind == CE_ATOM_LIST)
        for (size_t i = 0; i < a->len; ++i) if (!template_check(r,a->items[i],error)) return false;
    return true;
}

static bool contract(CeMacros *m, MacroRule *r, CeAtom *lhs, CeAtom *rhs,
                      const CeProduction *production, const CeGrammar *surface, CeError *error) {
    bool ports = form(rhs,"Macro") && rhs->len == 8 && rhs->items[1]->kind == CE_ATOM_TEXT &&
        (ce_is(rhs->items[1]->text,"cesta-macro/2") || ce_is(rhs->items[1]->text,"cesta-macro/3"));
    r->type_parameters = ports && ce_is(rhs->items[1]->text,"cesta-macro/3");
    if (!ports && (!form(rhs,"Macro") || rhs->len != 7 || rhs->items[1]->kind != CE_ATOM_TEXT ||
        !ce_is(rhs->items[1]->text,"cesta-macro/1")))
        return ce_error(error,rhs->offset,"macro interpretation requires a versioned cesta-macro contract");
    if (lhs->kind != CE_ATOM_LIST || lhs->len != r->arity+1)
        return ce_error(error,lhs->offset,"macro left side disagrees with Terms arity");
    const CeAtom *profile = rhs->items[2], *args = rhs->items[3+ports], *result = rhs->items[4+ports],
                 *locals = rhs->items[5+ports], *body = rhs->items[6+ports];
    inventory(m,rhs);
    if (ports) {
        const CeAtom *bindings = rhs->items[3];
        if (!form(bindings,"Ports")) return ce_error(error,bindings->offset,"version 2 requires Ports");
        r->nports = bindings->len-1; r->ports = ce_alloc(&m->arena,r->nports*sizeof(*r->ports));
        for (size_t i = 0; i < r->nports; ++i) {
            const CeAtom *p = bindings->items[i+1]; MacroPort *binding = &r->ports[i];
            if (form(p,"CompilerBuiltin")) {
                if (!r->type_parameters || !field(p,"CompilerBuiltin",2,error) ||
                    !variable(p->items[1],&binding->key,error) || !text(p->items[2],&binding->name,error))
                    return ce_error(error,p->offset,"CompilerBuiltin requires version 3");
                binding->intrinsic = true;
                if (binding->name.len < 11 || memcmp(binding->name.data,"__builtin_",10))
                    return ce_error(error,p->offset,"CompilerBuiltin requires a native __builtin_ name");
            } else {
              if (!field(p,"NativeSymbol",4,error) || !variable(p->items[1],&binding->key,error) ||
                !field(p->items[2],"SystemHeader",1,error) ||
                !text(p->items[2]->items[1],&binding->header,error) ||
                !ctype(p->items[3],error) || !text(p->items[4],&binding->name,error)) return false;
              if (!binding->header.len) return ce_error(error,p->offset,"native port requires its declaring header");
              for (size_t c = 0; c < binding->header.len; ++c)
                if (binding->header.data[c] <= 32 || binding->header.data[c] == '>' ||
                    binding->header.data[c] == '<' || binding->header.data[c] == '\\')
                    return ce_error(error,p->offset,"invalid system header spelling");
              binding->type = p->items[3];
            }
            if (!identifier(binding->name)) return ce_error(error,p->offset,"native symbol requires a C identifier");
            for (size_t j = 0; j < i; ++j) if (ce_equal(binding->key,r->ports[j].key))
                return ce_error(error,p->offset,"duplicate definition-site port");
            binding->type_name = fresh(m); binding->alias = fresh(m);
        }
    }
    CeText dialect, category;
    if (!field(profile,"Profile",1,error) || !text(profile->items[1],&dialect,error)) return false;
    if (!ce_is(dialect,"c11") && !ce_is(dialect,"gnu11"))
        return ce_error(error,profile->offset,"macro profile must be c11 or gnu11");
    r->gnu = ce_is(dialect,"gnu11");
    for (size_t i = 0; i < r->nports; ++i)
        if (r->ports[i].intrinsic && !r->gnu)
            return ce_error(error,profile->offset,"compiler builtin ports require the GNU profile");
    if (!form(args,"Arguments") || args->len != r->arity+1)
        return ce_error(error,args->offset,"Arguments must cover every Terms field in order");
    r->arguments = ce_alloc(&m->arena,r->arity*sizeof(*r->arguments));
    for (size_t i = 0; i < r->arity; ++i) {
        const CeAtom *a = args->items[i+1]; MacroArgument *arg = &r->arguments[i];
        if (!field(a,"Arg",4,error) || !variable(a->items[1],&arg->name,error) ||
            !ctype(a->items[2],error) || !text(a->items[3],&category,error)) return false;
        if (lhs->items[i+1]->kind != CE_ATOM_SYMBOL || !ce_equal(lhs->items[i+1]->text,arg->name))
            return ce_error(error,a->offset,"Arg name must agree with the corresponding left-side hole");
        for (size_t j = 0; j < i; ++j) if (ce_equal(arg->name,r->arguments[j].name))
            return ce_error(error,a->offset,"duplicate macro argument");
        int source_sort = production->rhs[i+2];
        arg->sort = named(m->grammar,surface->symbols[source_sort].name);
        if (arg->sort < 0) return ce_error(error,a->offset,"argument sort is outside the C signature");
        arg->type = a->items[2];
        if (ce_is(category,"value")) arg->category = CATEGORY_VALUE;
        else if (ce_is(category,"addressable")) arg->category = CATEGORY_ADDRESSABLE;
        else if (ce_is(category,"addressable-modifiable")) arg->category = CATEGORY_MODIFIABLE;
        else if (ce_is(category,"syntax")) arg->category = CATEGORY_SYNTAX;
        else return ce_error(error,a->offset,"unknown argument value category");
        const CeAtom *evaluation = a->items[4]; CeText policy;
        if (!form(evaluation,"Evaluation") || (evaluation->len != 2 && evaluation->len != 3) ||
            !text(evaluation->items[1],&policy,error)) return false;
        if (ce_is(policy,"once") && evaluation->len == 2) arg->evaluation = ARG_ONCE;
        else if (ce_is(policy,"deferred") && evaluation->len == 2) arg->evaluation = ARG_DEFERRED;
        else if (ce_is(policy,"syntax") && evaluation->len == 2) arg->evaluation = ARG_SYNTAX;
        else if (ce_is(policy,"repeat") && evaluation->len == 3) {
            arg->evaluation = ARG_REPEAT;
            if (!natural(evaluation->items[2],&arg->repetitions,error)) return false;
        } else return ce_error(error,evaluation->offset,"unknown argument evaluation contract");
        if ((arg->category == CATEGORY_SYNTAX) != (arg->evaluation == ARG_SYNTAX))
            return ce_error(error,a->offset,"syntax arguments require a syntax evaluation contract");
        if (arg->category != CATEGORY_SYNTAX &&
            !ce_is(m->grammar->symbols[m->grammar->symbols[arg->sort].sort].name,"Expr"))
            return ce_error(error,a->offset,"evaluated arguments must have Expr sort");
    }
    if (!field(result,"Result",2,error) || !ctype(result->items[1],error) ||
        !text(result->items[2],&category,error)) return false;
    r->type = result->items[1];
    CeText result_sort = m->grammar->symbols[m->grammar->symbols[r->result].sort].name;
    if ((!ce_is(result_sort,"Expr") || !ce_is(category,"value")) &&
        (!ce_is(result_sort,"Statement") || !ce_is(category,"statement")))
        return ce_error(error,result->offset,"macro result must be Expr/value or Statement/statement");
    if (!r->gnu && ce_is(result_sort,"Expr"))
        return ce_error(error,result->offset,"C11 expression lowering is not admitted yet; use a statement macro or GNU profile");
    if (!form(locals,"Locals")) return ce_error(error,locals->offset,"macro contract requires Locals");
    r->nlocals = locals->len-1; r->locals = ce_alloc(&m->arena,r->nlocals*sizeof(*r->locals));
    for (size_t i = 0; i < r->nlocals; ++i) {
        const CeAtom *a = locals->items[i+1]; MacroLocal *l = &r->locals[i];
        if (!field(a,"Local",2,error) || !variable(a->items[1],&l->name,error) ||
            !text(a->items[2],&l->space,error)) return false;
        if (!space(l->space)) return ce_error(error,a->offset,"unknown C binder namespace");
        for (size_t j = 0; j < i; ++j)
            if (ce_equal(l->name,r->locals[j].name) && ce_equal(l->space,r->locals[j].space))
                return ce_error(error,a->offset,"duplicate binder identity");
    }
    if (!field(body,"Template",1,error)) return false;
    r->body = body->items[1]; r->contract = rhs;
    if (!type_parameters_check(r,r->type,m->grammar,true,error)) return false;
    for (size_t i = 0; i < r->arity; ++i)
        if (!type_parameters_check(r,r->arguments[i].type,m->grammar,true,error)) return false;
    for (size_t i = 0; i < r->nports; ++i)
        if (!r->ports[i].intrinsic && !type_parameters_check(r,r->ports[i].type,m->grammar,false,error)) return false;
    if (!template_check(r,r->body,error)) return false;
    inventory(m,rhs);
    for (size_t i = 0; i < r->arity; ++i) {
        MacroArgument *a = &r->arguments[i];
        if (a->evaluation == ARG_REPEAT && a->uses != a->repetitions)
            return ce_error(error,r->body->offset,"repeat contract differs from Argument occurrence count");
        if (a->evaluation == ARG_DEFERRED && a->uses != 1)
            return ce_error(error,r->body->offset,"deferred argument must have one template occurrence");
    }
    return true;
}

static void reserve(CeMacros *m, CeText value) {
    for (size_t i = 0; i < m->nreserved; ++i) if (ce_equal(m->reserved[i],value)) return;
    m->reserved = ce_resize(m->reserved,m->nreserved+1,sizeof(*m->reserved));
    m->reserved[m->nreserved++] = ce_text(&m->arena,value.data,value.len);
}
static void inventory(CeMacros *m, const CeAtom *a) {
    if (a->kind == CE_ATOM_LIST) {
        for (size_t i = 0; i < a->len; ++i) inventory(m,a->items[i]);
        return;
    }
    /* Over-approximate written identifiers, including explicit native text and
       directive replacement spelling. Native header names require compiler
       admission; this source inventory does not claim to know their contents. */
    for (size_t i = 0; i < a->text.len;) {
        unsigned c = a->text.data[i];
        if (!(c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c >= 128)) { ++i; continue; }
        size_t start = i++;
        while (i < a->text.len && (identifier((CeText){a->text.data+i,1}) ||
               (a->text.data[i] >= '0' && a->text.data[i] <= '9'))) ++i;
        reserve(m,(CeText){a->text.data+start,i-start});
    }
}
void ce_macros_environment(CeMacros *m, CeText identifiers, bool unguarded) {
    CeAtom bytes = {.kind=CE_ATOM_TEXT,.text=identifiers};
    inventory(m,&bytes); m->unguarded = unguarded;
}
static CeText fresh(CeMacros *m) {
    for (;;) {
        char buffer[64]; snprintf(buffer,sizeof(buffer),"cesta_macro_%zu",m->serial++);
        CeText name = {(unsigned char *)buffer,strlen(buffer)}; bool found = false;
        for (size_t i = 0; i < m->nreserved; ++i) found |= ce_equal(m->reserved[i],name);
        if (!found) { reserve(m,name); return m->reserved[m->nreserved-1]; }
    }
}
CeMacros *ce_macros_new(const CeGrammar *g, const CeAtom *source, CeError *error) {
    CeMacros *m = calloc(1,sizeof(*m));
    if (!m) { ce_error(error,0,"cannot allocate macro context"); return NULL; }
    m->grammar = g; m->catalog.syntax = &m->syntax;
    if (!ce_grammar_read(&m->syntax,ce_builtin_TheoryModuleSyntax(),error) ||
        !ce_grammar_finish(&m->syntax,error) || !ce_builtin_view(g,"compact-c/2",&m->view,error)) {
        ce_macros_free(m); return NULL;
    }
    if (source) inventory(m,source);
    return m;
}
void ce_macros_free(CeMacros *m) {
    if (!m) return;
    ce_view_free(&m->view); ce_catalog_free(&m->catalog); ce_grammar_free(&m->syntax);
    for (size_t i = 0; i < m->len; ++i) free(m->rules[i].helpers);
    ce_arena_free(&m->arena); free(m->rules); free(m->reserved); free(m->safe);
    free(m->private_atoms); free(m->private_trees); free(m->guards); free(m);
}
bool ce_macros_load(CeMacros *m, const char *path, const char *theory, CeError *error) {
    CeModule *module; CePresentation presentation = {0}; CeGrammar surface = {0};
    size_t begin = m->len;
    if (!ce_module_load(&m->catalog,path,&module,error) || !ce_module_check(&m->catalog,module,error) ||
        !ce_module_theory(&m->catalog,module,theory,&presentation,error) ||
        !ce_presentation_signature(&m->catalog,&presentation,m->grammar,&surface,error)) {
        ce_grammar_free(&surface); return false;
    }
    bool ok = true;
    for (size_t i = 0; i < surface.nproductions && ok; ++i) {
        const CeProduction *p = &surface.productions[i];
        if (p->internal || surface.symbols[p->lhs].erased || surface.symbols[p->lhs].list_element >= 0) continue;
        if (p->len < 3 || surface.symbols[p->rhs[0]].kind != CE_LITERAL ||
            !ce_is(surface.symbols[p->rhs[0]].name,"(") ||
            surface.symbols[p->rhs[1]].kind != CE_LITERAL ||
            !ce_is(surface.symbols[p->rhs[p->len-1]].name,")")) {
            ok = ce_error(error,p->offset,"macro Terms require a headed S-expression"); break;
        }
        MacroRule r = {.label=p->label,.head=surface.symbols[p->rhs[1]].name,
                       .path=ce_module_path(module),.theory=ce_cstr(&m->arena,theory),.arity=p->len-3};
        r.result = named(m->grammar,surface.symbols[p->lhs].name);
        if (r.result < 0) { ok = ce_error(error,p->offset,"macro result sort is outside the C signature"); break; }
        for (size_t j = 0; j < r.arity; ++j)
            if (surface.symbols[p->rhs[j+2]].kind == CE_LITERAL)
                ok = ce_error(error,p->offset,"macro field requires a declared syntax sort");
        if (!ok) break;
        for (size_t j = 0; j < m->len; ++j)
            if (same(m->grammar,r.result,m->rules[j].result) && ce_equal(r.head,m->rules[j].head))
                ok = ce_error(error,p->offset,"duplicate macro dispatch head in the same result sort");
        for (size_t j = 0; j < m->view.len; ++j)
            if (same(m->grammar,r.result,m->view.rules[j].result) &&
                ce_equal(r.head,m->view.rules[j].head) && m->view.rules[j].shape == CE_VIEW_FORM)
                ok = ce_error(error,p->offset,"macro head would change an existing compact form");
        const char *reserved[] = {"include","define","undef","cpp-if","cpp-ifdef","cpp-ifndef","comment","native","use-macros"};
        for (size_t j = 0; j < sizeof(reserved)/sizeof(*reserved); ++j)
            if (ce_is(r.head,reserved[j])) ok = ce_error(error,p->offset,"macro head is a native source form");
        if (!ok) break;
        bool found = false;
        for (size_t j = 0; j < presentation.nlaws && ok; ++j) {
            const CeTree *law = presentation.laws[j].tree;
            if (ce_tree_label(&m->syntax,law,"RDecl")) law = law->children[1];
            if (!ce_tree_label(&m->syntax,law,"EquationImpl") && !ce_tree_label(&m->syntax,law,"RewriteBase"))
                continue;
            CeAtom *lhs = source_atom(m,law->children[0],error);
            if (!lhs) { ok = false; break; }
            if (lhs->kind != CE_ATOM_LIST || !lhs->len || !ce_equal(lhs->items[0]->text,p->label)) continue;
            if (found) { ok = ce_error(error,law->offset,"duplicate macro interpretation"); break; }
            CeAtom *rhs = source_atom(m,law->children[1],error);
            ok = rhs && contract(m,&r,lhs,rhs,p,&surface,error); found = true;
        }
        if (ok && !found) ok = ce_error(error,p->offset,"macro Terms constructor has no contract");
        if (ok) ok = definition_shape(m,&r,error);
        if (ok) {
            /* Source grammar storage is temporary; retain its names in the
               macro arena before freeing it. */
            r.label = ce_text(&m->arena,r.label.data,r.label.len);
            r.head = ce_text(&m->arena,r.head.data,r.head.len);
            m->rules = ce_resize(m->rules,m->len+1,sizeof(*m->rules)); m->rules[m->len++] = r;
        }
    }
    for (size_t j = 0; j < presentation.nlaws && ok; ++j) {
        const CeTree *law = presentation.laws[j].tree;
        if (ce_tree_label(&m->syntax,law,"RDecl")) law = law->children[1];
        if (!ce_tree_label(&m->syntax,law,"EquationImpl") && !ce_tree_label(&m->syntax,law,"RewriteBase")) {
            ok = ce_error(error,law->offset,"macro law requires an equation or rewrite"); break;
        }
        CeAtom *lhs = source_atom(m,law->children[0],error);
        bool found = false;
        if (lhs && lhs->kind == CE_ATOM_LIST && lhs->len)
            for (size_t i = begin; i < m->len; ++i)
                found |= ce_equal(lhs->items[0]->text,m->rules[i].label);
        if (!found) ok = ce_error(error,law->offset,"macro law names no Terms constructor");
    }
    ce_grammar_free(&surface); return ok;
}
bool ce_macros_accepts(const CeMacros *m, const CeAtom *a, int sort) {
    if (!m) return false;
    for (size_t i = 0; i < m->nprivate; ++i)
        if (m->private_atoms[i] == a && same(m->grammar,sort,m->private_trees[i]->symbol)) return true;
    if (a->kind != CE_ATOM_LIST || !a->len || a->items[0]->kind != CE_ATOM_SYMBOL) return false;
    for (size_t i = 0; i < m->len; ++i)
        if (same(m->grammar,sort,m->rules[i].result) && ce_equal(a->items[0]->text,m->rules[i].head)) return true;
    return false;
}

static bool private_accepts(void *context, const CeAtom *a, int sort) {
    CeMacros *m = context;
    for (size_t i = 0; i < m->nprivate; ++i)
        if (m->private_atoms[i] == a && same(m->grammar,sort,m->private_trees[i]->symbol)) return true;
    return false;
}
static unsigned private_extension(void *context, const CeAtom *a, int sort, int parent,
                                  CeArena *arena, CeTree **out, CeAtomDecoder recurse,
                                  void *recurse_context, CeError *error, size_t depth) {
    (void)parent; (void)arena; (void)recurse; (void)recurse_context; (void)error; (void)depth;
    CeMacros *m = context;
    for (size_t i = 0; i < m->nprivate; ++i)
        if (m->private_atoms[i] == a && same(m->grammar,sort,m->private_trees[i]->symbol)) {
            *out = m->private_trees[i]; return CE_EXTENSION_EXPANDED;
        }
    return CE_EXTENSION_UNHANDLED;
}

typedef struct {
    CeMacros *macros; MacroRule *rule; CeAtom **arguments; CeText *locals; CeArena *arena;
    bool checking;
} Instance;
static CeAtom *instantiate(Instance *in, const CeAtom *a, CeError *error) {
    if (form(a,"TypeArgument")) {
        if (!type_parameter(in->rule,a,in->macros->grammar,error)) return NULL;
        if (in->checking) return word(in->arena,"int");
        MacroArgument *arg = argument(in->rule,a->items[1]->text);
        return in->arguments[(size_t)(arg-in->rule->arguments)];
    }
    if (form(a,"Argument")) {
        MacroArgument *arg = argument(in->rule,a->items[1]->text);
        return in->arguments[(size_t)(arg-in->rule->arguments)];
    }
    if (form(a,"LocalRef")) {
        size_t i = local(in->rule,a->items[1]->text,a->items[2]->text);
        return symbol(in->arena,in->locals[i],a->offset);
    }
    if (form(a,"Capture")) return symbol(in->arena,a->items[2]->text,a->offset);
    if (form(a,"DefinitionRef")) {
        MacroPort *binding = &in->rule->ports[port(in->rule,a->items[2]->text)];
        if (in->checking) return symbol(in->arena,binding->alias,a->offset);
        binding->used = true;
        CeAtom *reference = list(in->arena,"*",1);
        reference->items[1] = symbol(in->arena,binding->alias,a->offset);
        return reference;
    }
    if (form(a,"CompilerRef")) {
        MacroPort *binding = &in->rule->ports[port(in->rule,a->items[1]->text)];
        if (in->checking) return symbol(in->arena,binding->alias,a->offset);
        binding->used = true;
        return symbol(in->arena,binding->name,a->offset);
    }
    CeAtom *out = ce_atom_new(in->arena,a->kind,ce_text(in->arena,a->text.data,a->text.len),a->len);
    out->offset = a->offset;
    for (size_t i = 0; i < a->len; ++i) {
        out->items[i] = instantiate(in,a->items[i],error);
        if (!out->items[i]) return NULL;
    }
    return out;
}
static CeAtom *type_slot(CeMacros *m, CeArena *arena, const CeAtom *a, CeText name, size_t *uses) {
    for (size_t i = 0; i < m->nprivate; ++i)
        if (m->private_atoms[i] == a) return (CeAtom *)a;
    if (a->kind == CE_ATOM_SYMBOL && ce_is(a->text,"TypeSlot")) { ++*uses; return symbol(arena,name,a->offset); }
    CeAtom *out = ce_atom_new(arena,a->kind,ce_text(arena,a->text.data,a->text.len),a->len);
    out->offset = a->offset;
    for (size_t i = 0; i < a->len; ++i) out->items[i] = type_slot(m,arena,a->items[i],name,uses);
    return out;
}
static bool type_alias(CeMacros *m, CeArena *arena, const CeAtom *type, CeText name,
                        FILE *file, CeError *error) {
    size_t uses = 0;
    CeAtom *declarator = type->len == 3 ? type_slot(m,arena,type->items[2],name,&uses) : symbol(arena,name,0);
    if (type->len == 3 && uses != 1)
        return ce_error(error,type->offset,"CType declarator must contain exactly one TypeSlot");
    CeAtom *decl = list(arena,"var",2); decl->items[1] = type->items[1]; decl->items[2] = declarator;
    int sort = named(m->grammar,ce_cstr(arena,"Declaration")); CeTree *tree;
    if (!ce_view_expand_open(&m->view,decl,sort,arena,&tree,private_extension,private_accepts,m,error)) return false;
    fputs("typedef ",file); return ce_tree_print_open(m->grammar,tree,sort,file,error);
}

typedef struct { Instance instance; CeText *captures; size_t ncaptures; const CeTree *body; } Shape;
static bool shape_accepts(void *context, const CeAtom *a, int sort) {
    Shape *shape = context;
    if (form(a,"MacroShapeBody"))
        return shape->body && same(shape->instance.macros->grammar,sort,shape->body->symbol);
    if (!form(a,"MacroShapeHole") || a->len != 2) return false;
    size_t index; CeError error = {{0},0};
    return natural(a->items[1],&index,&error) && index < shape->instance.rule->arity &&
        same(shape->instance.macros->grammar,sort,shape->instance.rule->arguments[index].sort);
}
static unsigned shape_extension(void *context, const CeAtom *a, int sort, int parent,
                                 CeArena *arena, CeTree **out, CeAtomDecoder recurse,
                                 void *recurse_context, CeError *error, size_t depth) {
    (void)parent; (void)recurse; (void)recurse_context; (void)depth;
    if (!shape_accepts(context,a,sort)) return CE_EXTENSION_UNHANDLED;
    Shape *shape = context;
    if (form(a,"MacroShapeBody")) { *out = (CeTree *)shape->body; return CE_EXTENSION_EXPANDED; }
    const CeGrammar *g = shape->instance.macros->grammar;
    CeText name = g->symbols[g->symbols[sort].sort].name;
    CeAtom *witness;
    if (ce_is(name,"Expr")) witness = word(arena,"0");
    else if (ce_is(name,"Statement")) witness = list(arena,"block",0);
    else if (ce_is(name,"DeclarationSpecifiers")) witness = word(arena,"int");
    else {
        ce_error(error,a->offset,"no definition-check witness for syntax sort %.*s",(int)name.len,name.data);
        return CE_EXTENSION_ERROR;
    }
    return ce_view_expand(&shape->instance.macros->view,witness,sort,arena,out,NULL,error) ?
        CE_EXTENSION_EXPANDED : CE_EXTENSION_ERROR;
}
static void capture_inventory(Shape *shape, const CeAtom *a) {
    if (form(a,"Capture") && ce_is(a->items[1]->text,"ordinary")) {
        shape->captures = ce_resize(shape->captures,shape->ncaptures+1,sizeof(*shape->captures));
        shape->captures[shape->ncaptures++] = a->items[2]->text; return;
    }
    for (size_t i = 0; i < a->len; ++i) capture_inventory(shape,a->items[i]);
}
static bool explicit_references(const Shape *shape, const CeTree *tree, CeError *error) {
    const CeGrammar *g = shape->instance.macros->grammar;
    if (ce_tree_label(g,tree,"Var")) {
        CeText name = tree->children[0]->spelling; bool allowed = false;
        for (size_t i = 0; i < shape->instance.rule->nlocals; ++i)
            allowed |= ce_is(shape->instance.rule->locals[i].space,"ordinary") &&
                ce_equal(name,shape->instance.locals[i]);
        for (size_t i = 0; i < shape->ncaptures; ++i) allowed |= ce_equal(name,shape->captures[i]);
        if (!allowed) return ce_error(error,tree->offset,"template identifier needs LocalRef, Capture or a definition-site port");
    }
    for (size_t i = 0; i < tree->len; ++i)
        if (!explicit_references(shape,tree->children[i],error)) return false;
    return true;
}
static bool binder_admission(Shape *shape, CeError *error) {
    CeMacros *m = shape->instance.macros; MacroRule *r = shape->instance.rule;
    CeArena *arena = shape->instance.arena;
    CeAtom *unit = list(arena,"unit",shape->ncaptures+1);
    for (size_t i = 0; i < shape->ncaptures; ++i) {
        CeAtom *decl = list(arena,"var",2);
        decl->items[1] = word(arena,"int"); decl->items[2] = symbol(arena,shape->captures[i],0);
        unit->items[i+1] = decl;
    }
    bool expr = ce_is(m->grammar->symbols[m->grammar->symbols[r->result].sort].name,"Expr");
    CeAtom *function = list(arena,"fn",expr ? 4 : 5);
    function->items[1] = word(arena,"int"); function->items[2] = word(arena,"cesta_binding_check");
    function->items[3] = ce_atom_new(arena,CE_ATOM_LIST,(CeText){0},0);
    CeAtom *body = list(arena,"MacroShapeBody",0), *ret = list(arena,"return",1);
    ret->items[1] = expr ? body : word(arena,"0");
    function->items[4] = expr ? ret : body;
    if (!expr) function->items[5] = ret;
    unit->items[shape->ncaptures+1] = function;
    CeTree *tree;
    if (!ce_view_expand_open(&m->view,unit,m->grammar->entries[0],arena,&tree,
            shape_extension,shape_accepts,shape,error)) return false;
    CeSema sema = {0};
    if (!ce_sema_init(&sema,m->grammar,(CeText){0},error)) return false;
    bool ok = ce_bind(&sema,tree,error);
    for (size_t i = 0; i < r->nlocals && ok; ++i) {
        MacroLocal *local = &r->locals[i]; CeEntity *owner = NULL;
        if (ce_is(local->space,"member")) {
            ok = ce_error(error,r->body->offset,"member binder admission requires a record-owner port"); break;
        }
        unsigned ns = ce_is(local->space,"tag") ? CE_SPACE_TAG :
                      ce_is(local->space,"label") ? CE_SPACE_LABEL : CE_SPACE_ORDINARY;
        for (size_t j = 0; j < sema.nentities && ok; ++j) {
            CeEntity *entity = sema.entities[j];
            if (!ce_equal(entity->name,shape->instance.locals[i])) continue;
            if (entity->space != ns) {
                ok = ce_error(error,r->body->offset,"LocalRef binder is declared in a different C namespace"); break;
            }
            if (owner && owner != entity) {
                ok = ce_error(error,r->body->offset,"one LocalRef identity names multiple C declarations"); break;
            }
            owner = entity;
        }
        if (ok && !owner) ok = ce_error(error,r->body->offset,"LocalRef identity has no C declaration");
        for (size_t j = 0; j < sema.nuses && ok; ++j) {
            const CeUse *use = &sema.uses[j];
            if (ce_equal(use->entity->name,shape->instance.locals[i]) && use->entity != owner)
                ok = ce_error(error,r->body->offset,"LocalRef resolves to a different declaration");
        }
    }
    ce_sema_free(&sema); return ok;
}
static bool definition_shape(CeMacros *m, MacroRule *r, CeError *error) {
    CeArena arena = {0}; Shape shape = {.instance={.macros=m,.rule=r,.arena=&arena,.checking=true}};
    shape.instance.arguments = ce_alloc(&arena,r->arity*sizeof(*shape.instance.arguments));
    shape.instance.locals = ce_alloc(&arena,r->nlocals*sizeof(*shape.instance.locals));
    FILE *file = tmpfile();
    if (!file) return ce_error(error,r->body->offset,"cannot check C type declarations");
    bool ok = true;
    for (size_t i = 0; i < r->nports && ok; ++i) {
        shape.captures = ce_resize(shape.captures,shape.ncaptures+1,sizeof(*shape.captures));
        shape.captures[shape.ncaptures++] = r->ports[i].alias;
        if (!r->ports[i].intrinsic)
            ok = type_alias(m,&arena,r->ports[i].type,ce_cstr(&arena,"cesta_port_type_check"),file,error);
    }
    for (size_t i = 0; i < r->arity && ok; ++i) {
        char number[32]; snprintf(number,sizeof(number),"%zu",i);
        CeAtom *hole = list(&arena,"MacroShapeHole",1); hole->items[1] = word(&arena,number);
        shape.instance.arguments[i] = hole;
    }
    CeAtom *checked_type = instantiate(&shape.instance,r->type,error);
    ok = ok && checked_type && type_alias(m,&arena,checked_type,ce_cstr(&arena,"cesta_type_check"),file,error);
    for (size_t i = 0; i < r->arity && ok; ++i) {
        checked_type = instantiate(&shape.instance,r->arguments[i].type,error);
        ok = checked_type && type_alias(m,&arena,checked_type,ce_cstr(&arena,"cesta_argument_type_check"),file,error);
    }
    fclose(file);
    for (size_t i = 0; i < r->nlocals; ++i) {
        shape.instance.locals[i] = fresh(m);
    }
    capture_inventory(&shape,r->body);
    CeAtom *body = ok ? instantiate(&shape.instance,r->body,error) : NULL; CeTree *tree;
    ok = ok && body && ce_view_expand_open(&m->view,body,r->result,&arena,&tree,
        shape_extension,shape_accepts,&shape,error) && explicit_references(&shape,tree,error);
    if (ok) { shape.body = tree; ok = binder_admission(&shape,error); }
    free(shape.captures); ce_arena_free(&arena); return ok;
}
static bool safe_tree(const CeMacros *m, const CeTree *tree) {
    if (tree->production == CE_TREE_SOURCE) {
        for (size_t i = 0; i < m->nsafe; ++i) if (m->safe[i] == tree) return true;
        return false;
    }
    for (size_t i = 0; i < tree->len; ++i) if (!safe_tree(m,tree->children[i])) return false;
    return true;
}
static void guard(CeMacros *m, FILE *f, CeText name) {
    if (m->expanding) {
        for (size_t i = 0; i < m->nguards; ++i) if (ce_equal(m->guards[i],name)) return;
        m->guards = ce_resize(m->guards,m->nguards+1,sizeof(*m->guards));
        m->guards[m->nguards++] = name; return;
    }
    if (m->unguarded) fputs("\n#if 0\n#error Cesta_macro_name_is_a_preprocessor_macro\n#endif\n",f);
    else fprintf(f,"\n#if defined(%.*s)\n#error Cesta_macro_name_is_a_preprocessor_macro\n#endif\n",
                 (int)name.len,name.data);
}
static void admission_diagnostic(CeMacros *m, FILE *f, bool begin) {
    CeText name = begin ? m->diagnostic_begin : m->diagnostic_end;
    fprintf(f,"\n%.*s\n",(int)name.len,name.data);
}
static bool print_tree(const CeGrammar *g, const CeTree *t, FILE *f, CeError *error) {
    return ce_tree_print_open(g,t,t->symbol,f,error);
}
static CeAtom *private_argument(CeMacros *m, CeArena *arena, int sort, CeText parameter,
                                size_t offset, const CeAtom *written) {
    CeAtom *atom = list(arena,"MacroNativeArgument",1);
    atom->items[1] = symbol(arena,parameter,offset); atom->offset = offset;
    CeTree *tree = ce_alloc(arena,sizeof(*tree));
    tree->symbol = sort; tree->production = CE_TREE_SOURCE; tree->offset = offset;
    size_t n = m->unpack.len+parameter.len+2;
    tree->spelling = (CeText){ce_alloc(arena,n),n-1};
    snprintf((char *)tree->spelling.data,n,"%.*s %.*s",
             (int)m->unpack.len,m->unpack.data,(int)parameter.len,parameter.data);
    m->private_atoms = ce_resize(m->private_atoms,m->nprivate+1,sizeof(*m->private_atoms));
    m->private_trees = ce_resize(m->private_trees,m->nprivate+1,sizeof(*m->private_trees));
    m->private_atoms[m->nprivate] = atom; m->private_trees[m->nprivate++] = tree;
    m->safe = ce_resize(m->safe,m->nsafe+1,sizeof(*m->safe)); m->safe[m->nsafe++] = tree;
    if (ce_is(m->grammar->symbols[m->grammar->symbols[sort].sort].name,"DeclarationSpecifiers")) {
        const char *contexts[] = {"SpecifierQualifiers", "TypeName", "Parameter"};
        for (size_t i = 0; i < sizeof(contexts)/sizeof(*contexts); ++i) {
            int target = named(m->grammar,ce_cstr(arena,contexts[i]));
            CeTree *checked; CeError error = {{0},0};
            if (target < 0 || !ce_view_expand(&m->view,written,target,arena,&checked,NULL,&error)) continue;
            CeTree *adapted = ce_alloc(arena,sizeof(*adapted)); *adapted = *tree; adapted->symbol = target;
            m->private_atoms = ce_resize(m->private_atoms,m->nprivate+1,sizeof(*m->private_atoms));
            m->private_trees = ce_resize(m->private_trees,m->nprivate+1,sizeof(*m->private_trees));
            m->private_atoms[m->nprivate] = atom; m->private_trees[m->nprivate++] = adapted;
            m->safe = ce_resize(m->safe,m->nsafe+1,sizeof(*m->safe)); m->safe[m->nsafe++] = adapted;
        }
    }
    return atom;
}
static bool helper(MacroRule *r, CeMacros *m, CeText name, const CeText *parameters,
                    size_t arity, const char *body, size_t length, CeError *error) {
    char *buffer = NULL; size_t size = 0; FILE *file = open_memstream(&buffer,&size);
    if (!file) return ce_error(error,0,"cannot allocate native macro helper");
    fprintf(file,"#define %.*s(",(int)name.len,name.data);
    for (size_t i = 0; i < arity; ++i)
        fprintf(file,"%s%.*s",i ? "," : "",(int)parameters[i].len,parameters[i].data);
    fputs(") \\\n",file);
    for (size_t i = 0; i < length; ++i) {
        if (body[i] == '\n') fputs(" \\\n",file); else fputc(body[i],file);
    }
    fputc('\n',file);
    bool ok = !ferror(file); ok = fclose(file) == 0 && ok;
    if (ok) {
        r->helpers = ce_resize(r->helpers,r->nhelpers+1,sizeof(*r->helpers));
        r->helpers[r->nhelpers++] = ce_text(&m->arena,buffer,size);
    } else ce_error(error,0,"cannot write native macro helper");
    free(buffer); return ok;
}
static unsigned expand_rule(CeMacros *m, const CeAtom *a, int sort, int parent, CeArena *arena,
                          CeTree **out, CeAtomDecoder recurse, void *context, CeError *error, size_t depth) {
    if (!ce_macros_accepts(m,a,sort)) return CE_EXTENSION_UNHANDLED;
    MacroRule *r = NULL;
    for (size_t i = 0; i < m->len; ++i)
        if (same(m->grammar,sort,m->rules[i].result) && ce_equal(a->items[0]->text,m->rules[i].head)) { r=&m->rules[i]; break; }
    if (a->len != r->arity+1) {
        ce_error(error,a->offset,"macro %.*s requires %zu arguments",(int)r->head.len,r->head.data,r->arity);
        return CE_EXTENSION_ERROR;
    }
    bool expression = ce_is(m->grammar->symbols[m->grammar->symbols[sort].sort].name,"Expr");
    if (!m->unpack.len) {
        m->unpack = fresh(m); m->diagnostic_begin = fresh(m); m->diagnostic_end = fresh(m);
    }
    CeAtom **arguments = ce_alloc(arena,r->arity*sizeof(*arguments));
    CeTree **trees = ce_alloc(arena,r->arity*sizeof(*trees));
    CeTree **originals = ce_alloc(arena,r->arity*sizeof(*originals));
    CeText *parameters = ce_alloc(arena,r->arity*sizeof(*parameters));
    CeText *type_names = ce_alloc(arena,r->arity*sizeof(*type_names));
    CeText *names = ce_alloc(arena,r->arity*sizeof(*names)), *locals = ce_alloc(arena,r->nlocals*sizeof(*locals));
    for (size_t i = 0; i < r->nlocals; ++i) locals[i] = fresh(m);
    for (size_t i = 0; i < r->arity; ++i) {
        MacroArgument *arg = &r->arguments[i];
        if (!recurse(context,a->items[i+1],arg->sort,parent,arena,&originals[i],error,depth+1)) {
            char detail[sizeof(error->message)]; memcpy(detail,error->message,sizeof(detail));
            CeText expected = m->grammar->symbols[arg->sort].name;
            ce_error(error,error->offset,"macro %.*s argument %.*s requires sort %.*s: %.240s",
                (int)r->head.len,r->head.data,(int)arg->name.len,arg->name.data,
                (int)expected.len,expected.data,detail);
            return CE_EXTENSION_ERROR;
        }
        if (!safe_tree(m,originals[i])) {
            ce_error(error,a->items[i+1]->offset,"typed macro argument cannot contain opaque native source");
            return CE_EXTENSION_ERROR;
        }
        parameters[i] = fresh(m);
        arguments[i] = private_argument(m,arena,arg->sort,parameters[i],a->items[i+1]->offset,a->items[i+1]);
        trees[i] = m->private_trees[m->nprivate-1];
        if (arg->category == CATEGORY_SYNTAX) continue;
        type_names[i] = fresh(m);
        if (arg->evaluation == ARG_ONCE) {
            names[i] = fresh(m); arguments[i] = symbol(arena,names[i],a->items[i+1]->offset);
            if (arg->category != CATEGORY_VALUE) {
                CeAtom *indirection = list(arena,"*",1); indirection->items[1] = arguments[i]; arguments[i] = indirection;
            }
        }
    }
    Instance instance = {.macros=m,.rule=r,.arguments=arguments,.locals=locals,.arena=arena};
    CeAtom *body = instantiate(&instance,r->body,error);
    CeTree *body_tree;
    if (!body || !recurse(context,body,sort,parent,arena,&body_tree,error,depth+1)) return CE_EXTENSION_ERROR;
    char *buffer = NULL; size_t length = 0; FILE *file = open_memstream(&buffer,&length);
    if (!file) { ce_error(error,a->offset,"cannot allocate macro output"); return CE_EXTENSION_ERROR; }
    if (expression) fputs("__extension__ (",file);
    fputs("{\n",file); bool ok = true;
    for (size_t i = 0; i < r->nports; ++i)
        if (r->ports[i].intrinsic && r->ports[i].used) guard(m,file,r->ports[i].name);
    for (size_t i = 0; i < r->nlocals; ++i) guard(m,file,locals[i]);
    for (size_t i = 0; i < r->arity && ok; ++i) {
        MacroArgument *arg = &r->arguments[i];
        if (arg->category == CATEGORY_SYNTAX) continue;
        guard(m,file,type_names[i]);
        CeAtom *actual_type = instantiate(&instance,arg->type,error);
        ok = actual_type && type_alias(m,arena,actual_type,type_names[i],file,error); fputc('\n',file);
        if (!ok) break;
        fprintf(file,"_Static_assert(sizeof(%.*s), \"Cesta macro CType must have constant object size\");\n",
                (int)type_names[i].len,type_names[i].data);
        /* These expressions intentionally ask for a type without evaluating
           the user's effect. Keep suppression local to generated admission;
           diagnostics on executable template code remain enabled. */
        admission_diagnostic(m,file,true);
        fputs("_Static_assert(_Generic(",file);
        fputs(arg->category == CATEGORY_VALUE ? "(" : "&(",file);
        ok = print_tree(m->grammar,trees[i],file,error);
        fprintf(file,"), %.*s%s: 1, default: 0), \"Cesta macro argument type\");\n",
                (int)type_names[i].len,type_names[i].data,arg->category == CATEGORY_VALUE ? "" : "*");
        if (arg->category == CATEGORY_ADDRESSABLE) {
            fputs("(void)sizeof &(",file); ok = ok && print_tree(m->grammar,trees[i],file,error); fputs(");\n",file);
        } else if (arg->category == CATEGORY_MODIFIABLE) {
            fputs("(void)sizeof ((",file); ok = ok && print_tree(m->grammar,trees[i],file,error);
            fputs(") = (",file); ok = ok && print_tree(m->grammar,trees[i],file,error); fputs("));\n",file);
        }
        admission_diagnostic(m,file,false);
        if (arg->evaluation == ARG_ONCE) {
            guard(m,file,names[i]);
            fprintf(file,"%.*s %s%.*s = %s(",(int)type_names[i].len,type_names[i].data,
                arg->category == CATEGORY_VALUE ? "" : "*",(int)names[i].len,names[i].data,
                arg->category == CATEGORY_VALUE ? "" : "&");
            ok = ok && print_tree(m->grammar,trees[i],file,error); fputs(");\n",file);
        }
    }
    if (ok && expression) {
        CeText result_type = fresh(m); guard(m,file,result_type);
        CeAtom *actual_type = instantiate(&instance,r->type,error);
        ok = actual_type && type_alias(m,arena,actual_type,result_type,file,error); fputc('\n',file);
        fprintf(file,"_Static_assert(sizeof(%.*s), \"Cesta macro CType must have constant object size\");\n",
                (int)result_type.len,result_type.data);
        CeText result_helper = fresh(m), result_parameter = fresh(m);
        char *result_body = NULL; size_t result_size = 0;
        FILE *result_file = open_memstream(&result_body,&result_size);
        if (!result_file) ok = ce_error(error,a->offset,"cannot allocate result admission helper");
        else {
            admission_diagnostic(m,result_file,true);
            fprintf(result_file,"_Static_assert(_Generic((%.*s %.*s), %.*s: 1, default: 0), \"Cesta macro result type\");\n",
                (int)m->unpack.len,m->unpack.data,(int)result_parameter.len,result_parameter.data,
                (int)result_type.len,result_type.data);
            admission_diagnostic(m,result_file,false);
            fprintf(result_file,"%.*s %.*s",(int)m->unpack.len,m->unpack.data,
                    (int)result_parameter.len,result_parameter.data);
            ok = !ferror(result_file) && ok; ok = fclose(result_file) == 0 && ok;
            if (ok) ok = helper(r,m,result_helper,&result_parameter,1,result_body,result_size,error);
            free(result_body);
            fprintf(file,"%.*s((",(int)result_helper.len,result_helper.data);
            if (ok) ok = print_tree(m->grammar,body_tree,file,error);
            fputs("))",file);
        }
    }
    if (ok && !expression) ok = print_tree(m->grammar,body_tree,file,error);
    if (expression) fputs(";\n})",file); else fputs("\n}",file);
    ok = !ferror(file) && ok; ok = fclose(file) == 0 && ok;
    if (!ok) { free(buffer); return CE_EXTENSION_ERROR; }
    CeText helper_name = fresh(m);
    ok = helper(r,m,helper_name,parameters,r->arity,buffer,length,error); free(buffer);
    if (!ok) return CE_EXTENSION_ERROR;
    buffer = NULL; length = 0; file = open_memstream(&buffer,&length);
    if (!file) { ce_error(error,a->offset,"cannot allocate native macro call"); return CE_EXTENSION_ERROR; }
    fprintf(file,"%.*s(",(int)helper_name.len,helper_name.data);
    for (size_t i = 0; i < r->arity && ok; ++i) {
        if (i) fputc(',',file);
        fputc('(',file); ok = print_tree(m->grammar,originals[i],file,error); fputc(')',file);
    }
    fputc(')',file); ok = !ferror(file) && ok; ok = fclose(file) == 0 && ok;
    if (!ok) { free(buffer); return CE_EXTENSION_ERROR; }
    CeTree *tree = ce_alloc(arena,sizeof(*tree));
    tree->symbol = sort; tree->production = CE_TREE_SOURCE; tree->offset = a->offset;
    tree->spelling = ce_text(arena,buffer,length); free(buffer); *out = tree;
    m->safe = ce_resize(m->safe,m->nsafe+1,sizeof(*m->safe)); m->safe[m->nsafe++] = tree;
    return CE_EXTENSION_EXPANDED;
}
unsigned ce_macros_expand(CeMacros *m, const CeAtom *a, int sort, int parent, CeArena *arena,
                          CeTree **out, CeAtomDecoder recurse, void *context, CeError *error, size_t depth) {
    if (!m) return CE_EXTENSION_UNHANDLED;
    unsigned private_result = private_extension(m,a,sort,parent,arena,out,recurse,context,error,depth);
    if (private_result != CE_EXTENSION_UNHANDLED) return private_result;
    if (!ce_macros_accepts(m,a,sort)) return CE_EXTENSION_UNHANDLED;
    bool outer = m->expanding == 0;
    if (outer) m->nguards = 0;
    ++m->expanding;
    unsigned result = expand_rule(m,a,sort,parent,arena,out,recurse,context,error,depth);
    --m->expanding;
    if (outer && result == CE_EXTENSION_EXPANDED) {
        char *buffer = NULL; size_t length = 0; FILE *file = open_memstream(&buffer,&length);
        if (!file) { ce_error(error,a->offset,"cannot allocate native name guards"); return CE_EXTENSION_ERROR; }
        for (size_t i = 0; i < m->nguards; ++i) guard(m,file,m->guards[i]);
        fwrite((*out)->spelling.data,1,(*out)->spelling.len,file);
        bool ok = !ferror(file); ok = fclose(file) == 0 && ok;
        if (ok) (*out)->spelling = ce_text(arena,buffer,length);
        else ce_error(error,a->offset,"cannot write native name guards");
        free(buffer); if (!ok) return CE_EXTENSION_ERROR;
    }
    return result;
}
size_t ce_macros_dependencies(const CeMacros *m) { return m ? m->catalog.len : 0; }
CeText ce_macros_dependency(const CeMacros *m, size_t index) { return ce_module_path(m->catalog.modules[index]); }
size_t ce_macros_rules(const CeMacros *m) { return m ? m->len : 0; }
bool ce_macros_prelude(CeMacros *m, size_t begin, size_t end, CeArena *arena,
                       CeText *out, CeError *error) {
    char *buffer = NULL; size_t length = 0; FILE *file = open_memstream(&buffer,&length);
    if (!file) return ce_error(error,0,"cannot allocate definition-site declarations");
    bool ok = true;
    if (!m->prelude_emitted && m->unpack.len) {
        m->prelude_emitted = true;
        guard(m,file,m->unpack); guard(m,file,m->diagnostic_begin); guard(m,file,m->diagnostic_end);
        fprintf(file,"#define %.*s(...) __VA_ARGS__\n",(int)m->unpack.len,m->unpack.data);
        if (m->unguarded)
            fprintf(file,"#define %.*s\n#define %.*s\n",
                (int)m->diagnostic_begin.len,m->diagnostic_begin.data,
                (int)m->diagnostic_end.len,m->diagnostic_end.data);
        else fprintf(file,"#ifdef __clang__\n#define %.*s _Pragma(\"clang diagnostic push\") "
            "_Pragma(\"clang diagnostic ignored \\\"-Wunevaluated-expression\\\"\")\n"
            "#define %.*s _Pragma(\"clang diagnostic pop\")\n#else\n#define %.*s\n#define %.*s\n#endif\n",
            (int)m->diagnostic_begin.len,m->diagnostic_begin.data,(int)m->diagnostic_end.len,m->diagnostic_end.data,
            (int)m->diagnostic_begin.len,m->diagnostic_begin.data,(int)m->diagnostic_end.len,m->diagnostic_end.data);
    }
    for (size_t i = begin; i < end && ok; ++i)
        for (size_t j = 0; j < m->rules[i].nports && ok; ++j) {
            MacroPort *binding = &m->rules[i].ports[j];
            if (!binding->used) continue;
            if (binding->intrinsic) {
                guard(m,file,binding->name);
                fprintf(file,"\n#if !defined(__has_builtin)\n#error Cesta_compiler_requires_builtin_admission\n"
                    "#elif !__has_builtin(%.*s)\n#error Cesta_compiler_builtin_unavailable\n#endif\n",
                    (int)binding->name.len,binding->name.data);
                continue;
            }
            fprintf(file,"#include <%.*s>\n",(int)binding->header.len,binding->header.data);
            /* NativeSymbol captures a C entity, not a preprocessor alias.
               Preserve the caller's macro environment after acquiring it. */
            fprintf(file,"#pragma push_macro(\"%.*s\")\n#undef %.*s\n",
                (int)binding->name.len,binding->name.data,(int)binding->name.len,binding->name.data);
            guard(m,file,binding->type_name); guard(m,file,binding->alias);
            ok = type_alias(m,arena,binding->type,binding->type_name,file,error);
            fprintf(file,"\n_Static_assert(_Generic(&%.*s, %.*s*: 1, default: 0), \"Cesta native port type\");\n",
                (int)binding->name.len,binding->name.data,(int)binding->type_name.len,binding->type_name.data);
            fprintf(file,"static %.*s *const %.*s __attribute__((unused)) = &%.*s;\n",
                (int)binding->type_name.len,binding->type_name.data,(int)binding->alias.len,binding->alias.data,
                (int)binding->name.len,binding->name.data);
            fprintf(file,"#pragma pop_macro(\"%.*s\")\n",(int)binding->name.len,binding->name.data);
        }
    for (size_t i = begin; i < end && ok; ++i)
        for (size_t j = 0; j < m->rules[i].nhelpers; ++j)
            ok = fwrite(m->rules[i].helpers[j].data,1,m->rules[i].helpers[j].len,file) == m->rules[i].helpers[j].len;
    ok = !ferror(file) && ok; ok = fclose(file) == 0 && ok;
    if (ok) *out = ce_text(arena,buffer,length);
    free(buffer); return ok;
}
bool ce_macros_metadata(const CeMacros *m, FILE *file, CeError *error) {
    fputs("(macros cesta-macro/1",file);
    for (size_t i = 0; i < m->len; ++i) {
        const MacroRule *r = &m->rules[i];
        fputs("\n  (rule ",file); ce_quote(file,r->label); fputc(' ',file); ce_quote(file,r->head);
        fputc(' ',file); ce_quote(file,m->grammar->symbols[r->result].name);
        fputc(' ',file); ce_atom_write(r->contract,file); fputc(')',file);
    }
    fputs(")\n",file); return !ferror(file) || ce_error(error,0,"cannot write macro metadata");
}
int ce_macro_command(int argc, char **argv) {
    if (argc != 4) { fputs("usage: cesta check-macros MODULE THEORY\n",stderr); return 1; }
    CeGrammar grammar = {0}; CeError error = {{0},0}; CeMacros *macros = NULL; int result = 1;
    if (!ce_builtin_language(&grammar,true,&error) || !(macros=ce_macros_new(&grammar,NULL,&error)) ||
        !ce_macros_load(macros,argv[2],argv[3],&error) || !ce_macros_metadata(macros,stdout,&error))
        fprintf(stderr,"%s: byte %zu: %s\n",argv[2],error.offset,error.message);
    else result = 0;
    ce_macros_free(macros); ce_grammar_free(&grammar); return result;
}
