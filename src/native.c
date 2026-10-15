#define _POSIX_C_SOURCE 200809L
#include "native.h"
#include "macro.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

static bool head(const CeAtom *a, const char *name) {
    return a->kind == CE_ATOM_LIST && a->len &&
        a->items[0]->kind == CE_ATOM_SYMBOL && ce_is(a->items[0]->text,name);
}

bool ce_native_document(const CeAtom *a) {
    return head(a,"cesta") && a->len >= 2 &&
        a->items[1]->kind == CE_ATOM_SYMBOL && ce_is(a->items[1]->text,"native-c/1");
}

static bool bytes(FILE *f, CeText text) {
    return fwrite(text.data,1,text.len,f) == text.len;
}

/* Directive operands are C spelling, not grounded MeTTa values. Physical
   newlines require an explicit native fragment; they cannot inject directives
   into a structured single-line form. */
static bool operand(const CeAtom *a, FILE *f, CeError *error) {
    if (a->kind != CE_ATOM_SYMBOL && a->kind != CE_ATOM_TEXT)
        return ce_error(error,a->offset,"directive operand requires C spelling as a symbol or string");
    for (size_t i = 0; i < a->text.len; ++i)
        if (!a->text.data[i] || a->text.data[i] == '\n' || a->text.data[i] == '\r')
            return ce_error(error,a->offset,"directive operand must occupy one physical line");
    if (a->text.len && a->text.data[a->text.len-1] == '\\')
        return ce_error(error,a->offset,"directive operand cannot end in a line splice");
    return bytes(f,a->text);
}

static bool identifier(const CeAtom *a, FILE *f, CeError *error) {
    if (a->kind != CE_ATOM_SYMBOL || !a->text.len)
        return ce_error(error,a->offset,"directive name requires an identifier symbol");
    for (size_t i = 0; i < a->text.len; ++i) {
        unsigned c = a->text.data[i];
        if (!(c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (i && c >= '0' && c <= '9') || c >= 128))
            return ce_error(error,a->offset,"invalid directive identifier");
    }
    return bytes(f,a->text);
}

static bool finish(CeArena *arena, FILE *file, char **buffer, size_t *length,
                    CeText *out, CeError *error) {
    bool ok = !ferror(file);
    if (fclose(file)) ok = false;
    if (ok) *out = ce_text(arena,*buffer,*length);
    free(*buffer);
    return ok || ce_error(error,0,"cannot finish native C output");
}

typedef struct {
    const CeAtom *atom;
    size_t begin, end;
    CeTree *site;
} NativeImport;
typedef struct {
    const CeGrammar *grammar; CeMacros *macros;
    NativeImport *imports; size_t nimports;
} Native;

static bool accepts(void *context, const CeAtom *a, int sort) {
    static const char *names[] = {"include","define","undef","cpp-if","cpp-ifdef","cpp-ifndef","comment","native","use-macros"};
    Native *native = context;
    const CeSymbol *symbol = &native->grammar->symbols[sort];
    if (symbol->kind != CE_NONTERMINAL || symbol->list_element >= 0) return false;
    if (ce_macros_accepts(native->macros,a,sort)) return true;
    for (size_t i = 0; i < sizeof(names)/sizeof(*names); ++i) if (head(a,names[i])) return true;
    return false;
}

static bool branch(Native *native, const CeAtom *a, size_t begin, int sort, int parent,
                     CeArena *arena, FILE *file, CeAtomDecoder recurse, void *context,
                     CeError *error, size_t depth) {
    for (size_t i = begin; i < a->len; ++i) {
        CeTree *tree;
        if (!recurse(context,a->items[i],sort,parent,arena,&tree,error,depth+1) ||
            !ce_tree_print_open(native->grammar,tree,sort,file,error)) return false;
        fputc('\n',file);
    }
    return true;
}

static bool conditional(Native *native, const CeAtom *a, int sort, int parent,
                          CeArena *arena, FILE *file, CeAtomDecoder recurse, void *context,
                          CeError *error, size_t depth) {
    if (a->len < 3 || !head(a->items[2],"then"))
        return ce_error(error,a->offset,"conditional requires an operand and a (then ...) branch");
    const char *word = head(a,"cpp-if") ? "if" : head(a,"cpp-ifdef") ? "ifdef" : "ifndef";
    fprintf(file,"#%s ",word);
    if (!(head(a,"cpp-if") ? operand(a->items[1],file,error) : identifier(a->items[1],file,error))) return false;
    fputc('\n',file);
    if (!branch(native,a->items[2],1,sort,parent,arena,file,recurse,context,error,depth)) return false;
    bool otherwise = false;
    for (size_t i = 3; i < a->len; ++i) {
        const CeAtom *part = a->items[i]; size_t begin;
        if (otherwise) return ce_error(error,part->offset,"else must be the last conditional branch");
        if (head(part,"elif") && part->len >= 2) {
            fputs("#elif ",file);
            if (!operand(part->items[1],file,error)) return false;
            fputc('\n',file); begin = 2;
        } else if (head(part,"else")) {
            fputs("#else\n",file); otherwise = true; begin = 1;
        } else return ce_error(error,part->offset,"expected (elif CONDITION ...) or (else ...) branch");
        if (!branch(native,part,begin,sort,parent,arena,file,recurse,context,error,depth)) return false;
    }
    return fputs("#endif\n",file) >= 0;
}

static bool form(Native *native, const CeAtom *a, int sort, int parent, CeArena *arena,
                  FILE *file, CeAtomDecoder recurse, void *context, CeError *error, size_t depth) {
    if (head(a,"use-macros")) {
        CeText position = native->grammar->symbols[native->grammar->symbols[sort].sort].name;
        return (native->macros && ce_is(position,"ExternalDeclaration")) ||
            ce_error(error,a->offset,"use-macros must occur directly in the unit");
    }
    if (head(a,"cpp-if") || head(a,"cpp-ifdef") || head(a,"cpp-ifndef"))
        return conditional(native,a,sort,parent,arena,file,recurse,context,error,depth);
    if (head(a,"define")) {
        if (a->len != 3) return ce_error(error,a->offset,"define requires a name or parameter list and replacement spelling");
        fputs("#define ",file); const CeAtom *name = a->items[1];
        if (name->kind == CE_ATOM_LIST) {
            if (!name->len) return ce_error(error,name->offset,"function-like define requires a macro name");
            if (!identifier(name->items[0],file,error)) return false;
            fputc('(',file);
            for (size_t i = 1; i < name->len; ++i) {
                if (i > 1) fputc(',',file);
                if (name->items[i]->kind == CE_ATOM_SYMBOL && ce_is(name->items[i]->text,"...")) {
                    if (i+1 != name->len) return ce_error(error,name->items[i]->offset,"variadic parameter must be last");
                    fputs("...",file);
                } else if (!identifier(name->items[i],file,error)) return false;
            }
            fputc(')',file);
        } else if (!identifier(name,file,error)) return false;
        fputc(' ',file);
        if (!operand(a->items[2],file,error)) return false;
        return fputc('\n',file) != EOF;
    }
    if (a->len != 2) return ce_error(error,a->offset,"native source form requires one operand");
    const CeAtom *value = a->items[1];
    if (head(a,"include")) {
        fputs("#include ",file);
        if (value->kind == CE_ATOM_TEXT) {
            if (memchr(value->text.data,'"',value->text.len) || memchr(value->text.data,'\\',value->text.len))
                return ce_error(error,value->offset,"quoted include requires an unescaped header name");
            fputc('"',file);
            if (!operand(value,file,error)) return false;
            fputc('"',file);
        } else if (!operand(value,file,error)) return false;
        return fputc('\n',file) != EOF;
    }
    if (head(a,"undef")) {
        fputs("#undef ",file);
        return identifier(value,file,error) && fputc('\n',file) != EOF;
    }
    if (value->kind != CE_ATOM_TEXT) return ce_error(error,value->offset,"native and comment forms require a string");
    if (memchr(value->text.data,0,value->text.len)) return ce_error(error,value->offset,"NUL byte in native C source");
    if (head(a,"comment")) {
        for (size_t i = 1; i < value->text.len; ++i)
            if (value->text.data[i-1] == '*' && value->text.data[i] == '/')
                return ce_error(error,value->offset+i-1,"comment text contains its closing delimiter");
        fputs("/*",file); bytes(file,value->text); fputs("*/\n",file); return true;
    }
    /* Opaque expression fragments are grouped so they cannot change the
       surrounding grammar production's operator precedence. */
    CeText sort_name = native->grammar->symbols[native->grammar->symbols[sort].sort].name;
    bool expression = ce_is(sort_name,"Expr");
    if (expression) fputc('(',file);
    bytes(file,value->text);
    if (expression) fputc(')',file);
    fputc('\n',file); return true;
}

static unsigned extension(void *context, const CeAtom *a, int sort, int parent,
                            CeArena *arena, CeTree **out, CeAtomDecoder recurse,
                            void *recurse_context, CeError *error, size_t depth) {
    Native *native = context;
    unsigned macro = ce_macros_expand(native->macros,a,sort,parent,arena,out,recurse,recurse_context,error,depth);
    if (macro != CE_EXTENSION_UNHANDLED) return macro;
    static const char *names[] = {"include","define","undef","cpp-if","cpp-ifdef","cpp-ifndef","comment","native","use-macros"};
    bool known = false;
    for (size_t i = 0; i < sizeof(names)/sizeof(*names); ++i) known |= head(a,names[i]);
    if (!known) return CE_EXTENSION_UNHANDLED;
    const CeSymbol *symbol = &native->grammar->symbols[sort];
    if (symbol->kind != CE_NONTERMINAL || symbol->list_element >= 0) {
        ce_error(error,a->offset,"source form requires a C syntax position, not a token or list slot");
        return CE_EXTENSION_ERROR;
    }
    char *buffer = NULL; size_t length = 0; FILE *file = open_memstream(&buffer,&length);
    if (!file) { ce_error(error,a->offset,"cannot allocate native C output"); return CE_EXTENSION_ERROR; }
    bool ok = form(native,a,sort,parent,arena,file,recurse,recurse_context,error,depth);
    CeText spelling;
    if (!ok) { fclose(file); free(buffer); return CE_EXTENSION_ERROR; }
    if (!finish(arena,file,&buffer,&length,&spelling,error)) return CE_EXTENSION_ERROR;
    CeTree *tree = ce_alloc(arena,sizeof(*tree));
    tree->symbol = sort; tree->production = CE_TREE_SOURCE; tree->offset = a->offset; tree->spelling = spelling;
    if (head(a,"use-macros"))
        for (size_t i = 0; i < native->nimports; ++i)
            if (native->imports[i].atom == a) native->imports[i].site = tree;
    *out = tree; return CE_EXTENSION_EXPANDED;
}

bool ce_native_text_at(const CeGrammar *g, CeArena *arena, CeText source,
                       const char *presentation, const char *identity,
                       const CeNativeEnvironment *environment,
                       CeNativeOutput *out, CeError *error) {
    CeAtom *atom;
    if (!ce_atom_read(arena,source,&atom,error)) return false;
    if (ce_wire_envelope(atom) && !ce_wire_unpack(arena,atom,&atom,error)) return false;
    const char *key = "compact-c/2";
    if (ce_native_document(atom)) {
        if (atom->len != 4 || atom->items[2]->kind != CE_ATOM_SYMBOL)
            return ce_error(error,atom->offset,"native document requires (cesta native-c/1 PRESENTATION BODY)");
        key = (const char *)atom->items[2]->text.data; atom = atom->items[3];
        if (presentation && strcmp(presentation,"native-c/1"))
            return ce_error(error,atom->offset,"native document disagrees with the requested presentation");
    } else if (!presentation || strcmp(presentation,"native-c/1"))
        return ce_error(error,atom->offset,"native source forms require a native-c/1 document or presentation selection");
    CeViewProfile profile = {0}; CeTree *tree; Native native = {.grammar=g};
    if (head(atom,"unit")) native.imports = ce_alloc(arena,atom->len*sizeof(*native.imports));
    bool ok = true;
    if (head(atom,"unit")) for (size_t i = 1; i < atom->len && ok; ++i) {
        const CeAtom *use = atom->items[i];
        if (!head(use,"use-macros")) continue;
        if (use->len != 3 || use->items[1]->kind != CE_ATOM_TEXT || use->items[2]->kind != CE_ATOM_SYMBOL) {
            ok = ce_error(error,use->offset,"use-macros requires a module filename and theory name"); break;
        }
        const char *name = (const char *)use->items[1]->text.data;
        const char *path = name;
        if (name[0] != '/' && identity) {
            const char *slash = strrchr(identity,'/');
            if (slash) {
                size_t prefix = (size_t)(slash-identity)+1;
                char *full = ce_alloc(arena,prefix+strlen(name)+1);
                memcpy(full,identity,prefix); strcpy(full+prefix,name); path = full;
            }
        }
        if (!native.macros) {
            native.macros = ce_macros_new(g,atom,error);
            if (native.macros && environment)
                ce_macros_environment(native.macros,environment->identifiers,environment->unguarded);
        }
        size_t begin = ce_macros_rules(native.macros);
        ok = native.macros && ce_macros_load(native.macros,path,(const char *)use->items[2]->text.data,error);
        if (ok) native.imports[native.nimports++] = (NativeImport){use,begin,ce_macros_rules(native.macros),NULL};
        if (!ok) error->offset = use->offset;
    }
    ok = ok && ce_builtin_view(g,key,&profile,error) &&
        ce_view_expand_open(&profile,atom,g->entries[0],arena,&tree,extension,accepts,&native,error);
    for (size_t i = 0; i < native.nimports && ok; ++i) {
        NativeImport *use = &native.imports[i];
        if (!use->site) ok = ce_error(error,use->atom->offset,"macro import has no source position");
        else ok = ce_macros_prelude(native.macros,use->begin,use->end,arena,&use->site->spelling,error);
    }
    if (ok) {
        char *buffer = NULL; size_t length = 0; FILE *file = open_memstream(&buffer,&length);
        if (!file) ok = ce_error(error,0,"cannot allocate native C output");
        else {
            ok = ce_tree_print_open(g,tree,g->entries[0],file,error);
            fputc('\n',file);
            if (ok) ok = finish(arena,file,&buffer,&length,&out->text,error);
            else { fclose(file); free(buffer); }
        }
    }
    if (ok && native.macros) {
        out->ndependencies = ce_macros_dependencies(native.macros);
        out->dependencies = ce_alloc(arena,out->ndependencies*sizeof(*out->dependencies));
        for (size_t i = 0; i < out->ndependencies; ++i) {
            CeText path = ce_macros_dependency(native.macros,i);
            out->dependencies[i] = ce_text(arena,path.data,path.len);
        }
    }
    ce_macros_free(native.macros); ce_view_free(&profile); return ok;
}
bool ce_native_text(const CeGrammar *g, CeArena *arena, CeText source,
                     const char *presentation, CeText *out, CeError *error) {
    CeNativeOutput result = {0};
    bool ok = ce_native_text_at(g,arena,source,presentation,NULL,NULL,&result,error);
    if (ok) *out = result.text;
    return ok;
}

int ce_expand_command(int argc, char **argv) {
    const char *path = NULL, *output = NULL, *presentation = NULL; bool gnu = true;
    const char *compiler = getenv("CESTA_CC"), *cache = getenv("CESTA_BUILD_DIR");
    if (!compiler || !*compiler) compiler = "cc";
    if (!cache || !*cache) cache = "build/cesta-generated";
    CeArena arena = {0}; CeGrammar grammar = {0}; CeError error = {{0},0}; CeText source = {0}, text;
    int result = 1; CeNativeOutput expanded = {0};
    char **flags = ce_alloc(&arena,((size_t)argc+5)*sizeof(*flags)); size_t nflags = 1;
    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i],"--")) {
            while (++i < argc) flags[nflags++] = argv[i];
            break;
        }
        if (!strcmp(argv[i],"--cc")) {
            if (++i == argc) { ce_error(&error,0,"--cc requires a compiler"); goto done; }
            compiler = argv[i]; continue;
        }
        if (!strcmp(argv[i],"-o") || !strcmp(argv[i],"--presentation")) {
            bool is_output = !strcmp(argv[i],"-o");
            if (++i == argc) { ce_error(&error,0,"expand option requires a value"); goto done; }
            if (is_output) output = argv[i]; else presentation = argv[i];
        } else if (!strcmp(argv[i],"--std=c11")) { gnu = false; flags[nflags++] = "-std=c11"; }
        else if (!strcmp(argv[i],"--std=gnu11")) { gnu = true; flags[nflags++] = "-std=gnu11"; }
        else if (path) { ce_error(&error,0,"expand takes one input file"); goto done; }
        else path = argv[i];
    }
    if (!path) { ce_error(&error,0,"expand requires an input file"); goto done; }
    flags[0] = (char *)compiler;
    if (!ce_read_file(&arena,path,&source,&error) || !ce_builtin_language(&grammar,gnu,&error) ||
        !ce_native_prepare(&grammar,&arena,source,presentation,path,nflags,flags,cache,&expanded,&error)) goto done;
    text = expanded.text;
    FILE *file = output ? fopen(output,"wb") : stdout;
    if (!file) { ce_error(&error,0,"cannot open expanded C: %s",strerror(errno)); goto done; }
    bool ok = bytes(file,text);
    if (output) ok = fclose(file) == 0 && ok;
    if (!ok) { ce_error(&error,0,"cannot write expanded C"); goto done; }
    result = 0;
done:
    if (result) {
        size_t line = 1, column = 1;
        for (size_t i = 0; i < error.offset && i < source.len; ++i)
            if (source.data[i] == '\n') { ++line; column = 1; } else ++column;
        fprintf(stderr,"%s:%zu:%zu: %s\n",path ? path : "cesta",line,column,error.message);
    }
    ce_grammar_free(&grammar); ce_arena_free(&arena); return result;
}
