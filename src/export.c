#define _POSIX_C_SOURCE 200809L
#include "export.h"
#include "macro.h"
#include "native.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* A cesta-export/1 manifest selects concrete instances of module definitions
   and publishes them as one ordinary C header:

     (cesta-export/1
       (header CT_SIZES_H)
       (profile gnu11)
       (include <stdint.h>)
       (use "Sizes.module" Checked)
       (function ct_doubled_capacity_u32 DoubledCapacity (count uint32_t)
         (doc "..."))
       (macro ct_probe Probe (doc "...")))

   The header is assembled as a native-c/1 document and printed by the same
   route as any Cesta unit; the native compiler still owns preprocessing. */

static bool head(const CeAtom *a, const char *name) {
    return a->kind == CE_ATOM_LIST && a->len && a->items[0]->kind == CE_ATOM_SYMBOL &&
        ce_is(a->items[0]->text,name);
}
static CeAtom *sym(CeArena *a, const char *text) {
    return ce_atom_new(a,CE_ATOM_SYMBOL,ce_cstr(a,text),0);
}
static CeAtom *txt(CeArena *a, CeText text) {
    return ce_atom_new(a,CE_ATOM_TEXT,ce_text(a,text.data,text.len),0);
}
static CeAtom *form(CeArena *a, const char *name, size_t n) {
    CeAtom *atom = ce_atom_new(a,CE_ATOM_LIST,(CeText){0},n+1);
    atom->items[0] = sym(a,name); return atom;
}
static CeText printf_text(CeArena *a, const char *format, CeText value) {
    size_t n = strlen(format)+value.len+1; char *buffer = ce_alloc(a,n);
    snprintf(buffer,n,format,(int)value.len,value.data);
    return (CeText){(unsigned char *)buffer,strlen(buffer)};
}
static bool c_identifier(CeText name) {
    if (!name.len) return false;
    for (size_t i = 0; i < name.len; ++i) {
        unsigned c = name.data[i];
        if (!(c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (i && c >= '0' && c <= '9')))
            return false;
    }
    return true;
}

typedef struct {
    CeAtom **items; size_t len;
} Items;
static void push(Items *items, CeAtom *atom) {
    items->items = ce_resize(items->items,items->len+1,sizeof(*items->items));
    items->items[items->len++] = atom;
}
/* Atoms own arena storage; build them at their final length. */
static CeAtom *list_of(CeArena *a, const char *name, const Items *items) {
    CeAtom *atom = form(a,name,items->len);
    for (size_t i = 0; i < items->len; ++i) atom->items[i+1] = items->items[i];
    return atom;
}
static void push_unique(CeText **names, size_t *len, CeText name) {
    for (size_t i = 0; i < *len; ++i) if (ce_equal((*names)[i],name)) return;
    *names = ce_resize(*names,*len+1,sizeof(**names)); (*names)[(*len)++] = name;
}

static bool relative_path(CeArena *a, const char *spec, CeText name, const char **out) {
    char *path = ce_alloc(a,name.len+1); memcpy(path,name.data,name.len); path[name.len] = 0;
    if (path[0] == '/') { *out = path; return true; }
    const char *slash = strrchr(spec,'/');
    if (!slash) { *out = path; return true; }
    size_t prefix = (size_t)(slash-spec)+1; char *full = ce_alloc(a,prefix+name.len+1);
    memcpy(full,spec,prefix); memcpy(full+prefix,name.data,name.len); full[prefix+name.len] = 0;
    *out = full; return true;
}

static bool write_atomically(const char *path, CeText text, CeError *error) {
    size_t n = strlen(path)+16; char *temporary = malloc(n);
    if (!temporary) return ce_error(error,0,"cannot allocate output path");
    snprintf(temporary,n,"%s.tmp%ld",path,(long)getpid());
    FILE *file = fopen(temporary,"wb");
    bool ok = file && fwrite(text.data,1,text.len,file) == text.len;
    if (file) ok = fclose(file) == 0 && ok;
    if (ok && rename(temporary,path)) ok = false;
    if (!ok) { remove(temporary); ce_error(error,0,"cannot write %s: %s",path,strerror(errno)); }
    free(temporary); return ok;
}

/* A counter table is structured rows, (ID NAME "spelling"), with a dense
   profile: IDs run 0..n-1 in order, and names and spellings are unique. One
   table yields the enumeration and, separately, the spelling array. */
typedef struct { size_t id; CeText name, spelling; } TableRow;
typedef struct {
    CeText type, prefix, count, names;
    TableRow *rows; size_t len;
} Table;

static bool table_symbol(const CeAtom *a, const char *field, CeText *out, CeError *error) {
    if (!head(a,field) || a->len != 2 || a->items[1]->kind != CE_ATOM_SYMBOL)
        return ce_error(error,a->offset,"table field %s requires one symbol",field);
    *out = a->items[1]->text; return true;
}
static bool read_table(CeArena *arena, const char *spec, const CeAtom *item, Table *table, CeError *error) {
    const char *path; CeText source; CeAtom *data;
    if (item->len != 2 || item->items[1]->kind != CE_ATOM_TEXT)
        return ce_error(error,item->offset,"table export requires a data filename");
    if (!relative_path(arena,spec,item->items[1]->text,&path) || !ce_read_file(arena,path,&source,error) ||
        !ce_atom_read(arena,source,&data,error)) { if (!error->offset) error->offset = item->offset; return false; }
    if (!head(data,"cesta-counters/1") || data->len != 7)
        return ce_error(error,data->offset,"counter data requires (cesta-counters/1 (enum T) (prefix P) (count C) (names N) (profile dense) (rows ...))");
    if (!table_symbol(data->items[1],"enum",&table->type,error) || !table_symbol(data->items[2],"prefix",&table->prefix,error) ||
        !table_symbol(data->items[3],"count",&table->count,error) || !table_symbol(data->items[4],"names",&table->names,error))
        return false;
    CeText profile;
    if (!table_symbol(data->items[5],"profile",&profile,error)) return false;
    if (!ce_is(profile,"dense")) return ce_error(error,data->items[5]->offset,"counter tables declare the dense profile");
    const CeAtom *rows = data->items[6];
    if (!head(rows,"rows") || rows->len < 2) return ce_error(error,rows->offset,"counter data requires nonempty (rows ...)");
    if (!c_identifier(table->type) || !c_identifier(table->prefix) || !c_identifier(table->count) || !c_identifier(table->names))
        return ce_error(error,data->offset,"counter data names must be C identifiers");
    table->len = rows->len-1; table->rows = ce_alloc(arena,table->len*sizeof(*table->rows));
    for (size_t i = 0; i < table->len; ++i) {
        const CeAtom *row = rows->items[i+1]; TableRow *r = &table->rows[i];
        if (row->kind != CE_ATOM_LIST || row->len != 3 || row->items[0]->kind != CE_ATOM_SYMBOL ||
            row->items[1]->kind != CE_ATOM_SYMBOL || row->items[2]->kind != CE_ATOM_TEXT)
            return ce_error(error,row->offset,"row requires (ID NAME \"spelling\")");
        CeText id = row->items[0]->text; r->id = 0;
        for (size_t k = 0; k < id.len; ++k) {
            if (id.data[k] < '0' || id.data[k] > '9' || (k == 0 && id.data[k] == '0' && id.len > 1) || id.len > 9)
                return ce_error(error,row->offset,"row ID must be a decimal natural number");
            r->id = r->id*10+(size_t)(id.data[k]-'0');
        }
        if (!id.len) return ce_error(error,row->offset,"row ID must be a decimal natural number");
        if (r->id != i) return ce_error(error,row->offset,"dense table expects ID %zu here, found %zu",i,r->id);
        r->name = row->items[1]->text; r->spelling = row->items[2]->text;
        if (!c_identifier(r->name)) return ce_error(error,row->offset,"row name must be a C identifier suffix");
        for (size_t k = 0; k < r->spelling.len; ++k) {
            unsigned c = r->spelling.data[k];
            if (c < 32 || c > 126 || c == '"' || c == '\\')
                return ce_error(error,row->offset,"row spelling must be printable ASCII without quotes or backslashes");
        }
        if (ce_equal(r->name,table->count)) return ce_error(error,row->offset,"row name collides with the count");
        for (size_t j = 0; j < i; ++j) {
            if (ce_equal(table->rows[j].name,r->name)) return ce_error(error,row->offset,"duplicate row name");
            if (ce_equal(table->rows[j].spelling,r->spelling)) return ce_error(error,row->offset,"duplicate row spelling");
        }
    }
    return true;
}
static CeAtom *table_enum(CeArena *arena, const Table *table) {
    char *text = NULL; size_t length = 0; FILE *file = open_memstream(&text,&length);
    if (!file) return NULL;
    fputs("typedef enum {\n",file);
    for (size_t i = 0; i < table->len; ++i)
        fprintf(file,"  %.*s%.*s = %zu,\n",(int)table->prefix.len,table->prefix.data,
                (int)table->rows[i].name.len,table->rows[i].name.data,table->rows[i].id);
    fprintf(file,"  %.*s%.*s = %zu\n} %.*s;",(int)table->prefix.len,table->prefix.data,
            (int)table->count.len,table->count.data,table->len,(int)table->type.len,table->type.data);
    if (fclose(file)) { free(text); return NULL; }
    CeAtom *block = form(arena,"native",1); block->items[1] = txt(arena,ce_text(arena,text,length));
    free(text); return block;
}
static CeAtom *table_names(CeArena *arena, const Table *table) {
    char *text = NULL; size_t length = 0; FILE *file = open_memstream(&text,&length);
    if (!file) return NULL;
    fprintf(file,"static const char *const %.*s[%.*s%.*s] = {\n",(int)table->names.len,table->names.data,
            (int)table->prefix.len,table->prefix.data,(int)table->count.len,table->count.data);
    for (size_t i = 0; i < table->len; ++i) {
        fprintf(file,"  [%.*s%.*s] = \"",(int)table->prefix.len,table->prefix.data,
                (int)table->rows[i].name.len,table->rows[i].name.data);
        /* Escape question marks so translation-phase trigraph replacement
           cannot change a printable spelling in C11. */
        for (size_t j = 0; j < table->rows[i].spelling.len; ++j) {
            unsigned c = table->rows[i].spelling.data[j];
            if (c == '?') fputc('\\',file);
            fputc((int)c,file);
        }
        fputs("\",\n",file);
    }
    fputs("};",file);
    if (fclose(file)) { free(text); return NULL; }
    CeAtom *block = form(arena,"native",1); block->items[1] = txt(arena,ce_text(arena,text,length));
    free(text); return block;
}

bool ce_export_header(const CeGrammar *g, CeArena *arena, const char *spec, FILE *plan, CeText *out, CeError *error) {
    CeText source; CeAtom *manifest;
    if (!ce_read_file(arena,spec,&source,error) || !ce_atom_read(arena,source,&manifest,error)) return false;
    if (!head(manifest,"cesta-export/1"))
        return ce_error(error,manifest->offset,"export specification requires (cesta-export/1 ...)");
    const CeAtom *guard = NULL; bool gnu = true; Items includes = {0}, entries = {0};
    CeMacros *macros = ce_macros_new(g,NULL,error); size_t begin = 0;
    CeText *protect = NULL, *builtins = NULL, *exported = NULL;
    size_t nprotect = 0, nbuiltins = 0, nexported = 0; bool ok = macros != NULL, theory = false;
    for (size_t i = 1; i < manifest->len && ok; ++i) {
        const CeAtom *item = manifest->items[i];
        if (head(item,"header")) {
            if (guard || item->len != 2 || item->items[1]->kind != CE_ATOM_SYMBOL || !c_identifier(item->items[1]->text))
                ok = ce_error(error,item->offset,"header requires one include-guard identifier");
            else guard = item->items[1];
        } else if (head(item,"profile")) {
            if (item->len != 2 || item->items[1]->kind != CE_ATOM_SYMBOL ||
                (!ce_is(item->items[1]->text,"gnu11") && !ce_is(item->items[1]->text,"c11")))
                ok = ce_error(error,item->offset,"profile is gnu11 or c11");
            else gnu = ce_is(item->items[1]->text,"gnu11");
        } else if (head(item,"include")) {
            push(&includes,(CeAtom *)item);
        } else if (head(item,"use")) {
            const char *path;
            if (theory) ok = ce_error(error,item->offset,"an export specification uses one theory");
            else if (item->len != 3 || item->items[1]->kind != CE_ATOM_TEXT || item->items[2]->kind != CE_ATOM_SYMBOL)
                ok = ce_error(error,item->offset,"use requires a module filename and theory name");
            else if (relative_path(arena,spec,item->items[1]->text,&path)) {
                begin = ce_macros_rules(macros); theory = true;
                ok = ce_macros_load(macros,path,(const char *)item->items[2]->text.data,error);
                if (!ok && !error->offset) error->offset = item->offset;
            }
        } else if (head(item,"function") || head(item,"macro")) {
            bool function = head(item,"function");
            if (!theory) { ok = ce_error(error,item->offset,"use must precede exported definitions"); break; }
            if (item->len < 3 || item->items[1]->kind != CE_ATOM_SYMBOL || item->items[2]->kind != CE_ATOM_SYMBOL ||
                !c_identifier(item->items[1]->text)) {
                ok = ce_error(error,item->offset,"export requires a C name and a constructor"); break;
            }
            CeText name = item->items[1]->text; const CeAtom *doc = NULL;
            for (size_t j = 0; j < nexported; ++j)
                if (ce_equal(exported[j],name)) { ok = ce_error(error,item->offset,"duplicate exported name"); break; }
            if (!ok) break;
            Items pairs = {0};
            for (size_t j = 3; j < item->len; ++j) {
                const CeAtom *field = item->items[j];
                if (head(field,"doc") && field->len == 2 && field->items[1]->kind == CE_ATOM_TEXT && !doc) doc = field->items[1];
                else if (field->kind == CE_ATOM_LIST && field->len == 2 && field->items[0]->kind == CE_ATOM_SYMBOL)
                    push(&pairs,(CeAtom *)field);
                else { ok = ce_error(error,field->offset,"expected (PARAMETER TYPE) or (doc TEXT)"); break; }
            }
            CeAtom *types = ce_atom_new(arena,CE_ATOM_LIST,(CeText){0},pairs.len);
            for (size_t j = 0; j < pairs.len; ++j) types->items[j] = pairs.items[j];
            free(pairs.items);
            if (!ok) break;
            CeMacroExport result = {0};
            CeMacroExportRequest request = {.name=name,.constructor=item->items[2]->text,.types=types,
                .function=function,.offset=item->offset,.plan=plan};
            ok = ce_macros_export(macros,begin,ce_macros_rules(macros),&request,arena,&result,error);
            if (!ok) break;
            exported = ce_resize(exported,nexported+1,sizeof(*exported)); exported[nexported++] = name;
            for (size_t j = 0; j < result.nprotected; ++j) push_unique(&protect,&nprotect,result.protected_names[j]);
            for (size_t j = 0; j < result.nbuiltins; ++j) push_unique(&builtins,&nbuiltins,result.builtins[j]);
            if (doc) {
                for (size_t j = 1; j < doc->text.len; ++j)
                    if (doc->text.data[j-1] == '*' && doc->text.data[j] == '/') { ok = ce_error(error,doc->offset,"documentation contains */"); break; }
                if (!ok) break;
                CeAtom *comment = form(arena,"native",1);
                comment->items[1] = txt(arena,printf_text(arena,"\n/* %.*s */",doc->text)); push(&entries,comment);
            }
            push(&entries,result.definition);
        } else if (head(item,"counters") || head(item,"counter-names")) {
            Table table = {0};
            if (!(ok = read_table(arena,spec,item,&table,error))) break;
            CeText name = head(item,"counters") ? table.type : table.names;
            for (size_t j = 0; j < nexported; ++j)
                if (ce_equal(exported[j],name)) { ok = ce_error(error,item->offset,"duplicate exported name"); break; }
            if (!ok) break;
            exported = ce_resize(exported,nexported+1,sizeof(*exported)); exported[nexported++] = name;
            CeAtom *block = head(item,"counters") ? table_enum(arena,&table) : table_names(arena,&table);
            if (!block) { ok = ce_error(error,item->offset,"cannot render counter table"); break; }
            CeAtom *spacer = form(arena,"native",1); spacer->items[1] = txt(arena,ce_cstr(arena,""));
            push(&entries,spacer); push(&entries,block);
        } else ok = ce_error(error,item->offset,"unknown export specification field");
    }
    if (ok && !guard) ok = ce_error(error,manifest->offset,"export specification requires (header GUARD)");
    if (ok && !nexported) ok = ce_error(error,manifest->offset,"export specification exports nothing");
    if (ok && !gnu && nbuiltins) ok = ce_error(error,manifest->offset,"compiler builtins require the gnu11 profile");
    if (ok) {
        /* Directives are grouped text; definitions stay structured and are
           printed by the C printer, each under its documentation. */
        char *prologue = NULL; size_t prologue_length = 0; FILE *text = open_memstream(&prologue,&prologue_length);
        if (!text) ok = ce_error(error,0,"cannot allocate export prologue");
        CeText name = guard ? guard->text : (CeText){0};
        if (ok) fprintf(text,"#ifndef %.*s\n#define %.*s 1\n",(int)name.len,name.data,(int)name.len,name.data);
        if (ok && includes.len) fputc('\n',text);
        for (size_t i = 0; ok && i < includes.len; ++i) {
            const CeAtom *value = includes.items[i]->len == 2 ? includes.items[i]->items[1] : NULL;
            if (value && value->kind == CE_ATOM_TEXT && !memchr(value->text.data,'"',value->text.len) &&
                !memchr(value->text.data,'\\',value->text.len) && !memchr(value->text.data,'\n',value->text.len))
                fprintf(text,"#include \"%.*s\"\n",(int)value->text.len,value->text.data);
            else if (value && value->kind == CE_ATOM_SYMBOL && value->text.len > 2 && value->text.data[0] == '<' &&
                     value->text.data[value->text.len-1] == '>')
                fprintf(text,"#include %.*s\n",(int)value->text.len,value->text.data);
            else ok = ce_error(error,includes.items[i]->offset,"include requires <header> or \"header\"");
        }
        if (ok) {
            fputs("\n#if ",text);
            for (size_t i = 0; i < nexported; ++i)
                fprintf(text,"%sdefined(%.*s)",i ? " || " : "",(int)exported[i].len,exported[i].data);
            fputs("\n#error an exported name is defined as a preprocessor macro\n#endif\n",text);
        }
        if (ok && nbuiltins) {
            fputs("#if ",text);
            for (size_t i = 0; i < nbuiltins; ++i)
                fprintf(text,"%sdefined(%.*s)",i ? " || " : "",(int)builtins[i].len,builtins[i].data);
            fputs("\n#error a required compiler builtin is defined as a preprocessor macro\n#endif\n"
                  "#if !defined(__has_builtin)\n#error the compiler cannot report its builtins\n#elif ",text);
            for (size_t i = 0; i < nbuiltins; ++i)
                fprintf(text,"%s!__has_builtin(%.*s)",i ? " || " : "",(int)builtins[i].len,builtins[i].data);
            fputs("\n#error the compiler does not provide a required builtin\n#endif\n",text);
        }
        if (ok && nprotect) {
            fputs("\n/* Parameter and local names below are this header's own, whatever macros the includer defines. */\n",text);
            for (size_t i = 0; i < nprotect; ++i)
                fprintf(text,"#pragma push_macro(\"%.*s\")\n#undef %.*s\n",(int)protect[i].len,protect[i].data,
                        (int)protect[i].len,protect[i].data);
        }
        if (text) ok = fclose(text) == 0 && ok;
        Items body = {0};
        if (ok) {
            CeAtom *block = form(arena,"native",1); block->items[1] = txt(arena,ce_text(arena,prologue,prologue_length-1));
            push(&body,block);
        }
        free(prologue);
        for (size_t i = 0; ok && i < entries.len; ++i) push(&body,entries.items[i]);
        char *epilogue = NULL; size_t epilogue_length = 0; FILE *tail = ok ? open_memstream(&epilogue,&epilogue_length) : NULL;
        if (ok && !tail) ok = ce_error(error,0,"cannot allocate export epilogue");
        if (ok) {
            fputc('\n',tail);
            for (size_t i = 0; i < nprotect; ++i)
                fprintf(tail,"#pragma pop_macro(\"%.*s\")\n",(int)protect[i].len,protect[i].data);
            fprintf(tail,"%s#endif",nprotect ? "\n" : "");
            ok = fclose(tail) == 0;
            CeAtom *block = form(arena,"native",1); block->items[1] = txt(arena,ce_text(arena,epilogue,epilogue_length));
            push(&body,block);
        }
        free(epilogue);
        const char *base = strrchr(spec,'/'); base = base ? base+1 : spec;
        CeAtom *banner = form(arena,"comment",1);
        banner->items[1] = txt(arena,printf_text(arena," Generated by cesta export-header from %.*s. Edit the specification or its modules and regenerate. ",
            ce_cstr(arena,base)));
        Items unit_items = {0}; push(&unit_items,banner);
        for (size_t i = 0; i < body.len; ++i) push(&unit_items,body.items[i]);
        CeAtom *unit = list_of(arena,"unit",&unit_items); free(unit_items.items);
        CeAtom *document = form(arena,"cesta",3);
        document->items[1] = sym(arena,"native-c/1"); document->items[2] = sym(arena,"compact-c/2"); document->items[3] = unit;
        char *buffer = NULL; size_t length = 0; FILE *file = ok ? open_memstream(&buffer,&length) : NULL;
        if (ok && !file) ok = ce_error(error,0,"cannot allocate export document");
        else if (ok) {
            ce_atom_write(document,file); ok = fclose(file) == 0;
            CeNativeOutput rendered = {0};
            if (ok) ok = ce_native_text_at(g,arena,ce_text(arena,buffer,length),NULL,spec,NULL,&rendered,error);
            if (ok) *out = rendered.text;
            free(buffer);
        }
        free(body.items);
    }
    free(includes.items); free(entries.items); free(protect); free(builtins); free(exported);
    ce_macros_free(macros); return ok;
}

int ce_export_command(int argc, char **argv) {
    const char *spec = NULL, *output = NULL, *plan_path = NULL; CeError error = {{0},0};
    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i],"-o") && i+1 < argc) output = argv[++i];
        else if (!strcmp(argv[i],"--plan") && i+1 < argc) plan_path = argv[++i];
        else if (!spec) spec = argv[i];
        else spec = NULL, i = argc;
    }
    if (!spec || !output) { fputs("usage: cesta export-header [--plan PLAN] EXPORT-SPEC -o OUTPUT.h\n",stderr); return 1; }
    FILE *plan = NULL;
    if (plan_path && !(plan = fopen(plan_path,"w"))) { fprintf(stderr,"cesta: cannot write %s\n",plan_path); return 1; }
    CeArena arena = {0}; CeGrammar grammar = {0}; CeText text; int result = 1;
    if (ce_builtin_language(&grammar,true,&error) && ce_export_header(&grammar,&arena,spec,plan,&text,&error) &&
        write_atomically(output,text,&error)) result = 0;
    else fprintf(stderr,"%s: byte %zu: %s\n",spec,error.offset,error.message);
    if (plan && fclose(plan) && !result) { fprintf(stderr,"cesta: cannot write %s\n",plan_path); result = 1; }
    ce_grammar_free(&grammar); ce_arena_free(&arena); return result;
}
