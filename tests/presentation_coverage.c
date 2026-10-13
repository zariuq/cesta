#include "../src/cesta.h"

#include <stdlib.h>
#include <string.h>

static unsigned checks;
static void require(bool condition, const char *message) {
    ++checks;
    if (!condition) { fprintf(stderr,"FAIL: %s\n",message); exit(1); }
}
static CeTree *fresh(CeArena *arena, int symbol, int production, size_t len) {
    CeTree *tree = ce_alloc(arena,sizeof(*tree));
    tree->symbol = symbol; tree->production = production; tree->len = len;
    tree->children = ce_alloc(arena,len*sizeof(*tree->children)); return tree;
}
static CeTree *inhabit(const CeGrammar *g, int symbol, int parent,
                       CeArena *arena, unsigned char *active);
static CeTree *constructor(const CeGrammar *g, size_t production,
                           CeArena *arena, unsigned char *active) {
    const CeProduction *p = &g->productions[production]; size_t len = 0;
    for (size_t i = 0; i < p->len; ++i)
        len += g->symbols[p->rhs[i]].kind != CE_LITERAL && !g->symbols[p->rhs[i]].erased;
    CeTree *tree = fresh(arena,p->lhs,(int)production,len); size_t field = 0;
    for (size_t i = 0; i < p->len; ++i) {
        int symbol = p->rhs[i];
        if (g->symbols[symbol].kind == CE_LITERAL || g->symbols[symbol].erased) continue;
        tree->children[field] = inhabit(g,symbol,(int)production,arena,active);
        if (!tree->children[field++]) return NULL;
    }
    if (ce_is(p->label,"_")) return len == 1 ? tree->children[0] : NULL;
    return tree;
}
static CeTree *inhabit(const CeGrammar *g, int symbol, int parent,
                       CeArena *arena, unsigned char *active) {
    const CeSymbol *s = &g->symbols[symbol];
    if (s->kind == CE_TOKEN) {
        const char *samples[] = {"x","1u","1.0","'a'","\"s\"","1","const","#pragma once"};
        for (size_t i = 0; i < sizeof(samples)/sizeof(*samples)+g->nsymbols; ++i) {
            CeText text;
            if (i < sizeof(samples)/sizeof(*samples)) text = ce_cstr(arena,samples[i]);
            else {
                const CeSymbol *literal = &g->symbols[i-sizeof(samples)/sizeof(*samples)];
                if (literal->kind != CE_LITERAL || !literal->name.len) continue;
                text = literal->name;
            }
            CeTokens tokens = {0}; CeError error = {{0},0};
            bool valid = ce_lex(g,text,&tokens,&error) && tokens.len == 1 &&
                ce_token_matches(g,parent,symbol,&tokens.data[0]);
            ce_tokens_free(&tokens);
            if (valid) { CeTree *tree = fresh(arena,symbol,-1,0); tree->spelling = text; return tree; }
        }
        return NULL;
    }
    if (active[symbol]) return NULL;
    active[symbol] = 1; CeTree *tree = NULL;
    if (s->list_element >= 0) {
        bool empty = false;
        for (size_t i = 0; i < g->nproductions; ++i)
            empty |= g->productions[i].lhs == symbol && ce_is(g->productions[i].label,"[]");
        tree = fresh(arena,symbol,-2,empty ? 0 : 1);
        if (!empty && !(tree->children[0] = inhabit(g,s->list_element,-1,arena,active))) tree = NULL;
    } else for (size_t i = 0; i < g->nproductions && !tree; ++i)
        if (g->productions[i].lhs == symbol && !g->productions[i].internal)
            tree = constructor(g,i,arena,active);
    active[symbol] = 0; return tree;
}

int main(int argc, char **argv) {
    require(argc <= 3,"coverage takes an output prefix and optional presentation key");
    const char *prefix = argc >= 2 ? argv[1] : "presentation-complete";
    bool cmera = argc == 3 && !strcmp(argv[2],"cmera-style/1");
    bool compact2 = argc == 3 && !strcmp(argv[2],"compact-c/2");
    require(argc < 3 || cmera || compact2,"coverage presentation key must be cmera-style/1 or compact-c/2");
    for (const unsigned char *at = (const unsigned char *)prefix; *at; ++at)
        require((*at >= 'a' && *at <= 'z') || (*at >= 'A' && *at <= 'Z') ||
            (*at >= '0' && *at <= '9') || *at == '-' || *at == '_',"output prefix is a filename component");
    CeArena arena = {0}; CeGrammar g = {0}, syntax = {0}; CeError error = {{0},0}; CeText source;
    require(ce_read_file(&arena,"build/CGNU.cf",&source,&error) &&
        ce_grammar_read(&g,source,&error) && ce_grammar_finish(&g,&error) &&
        ce_profile_load(&g,"grammar/CGNU.profile",&error),error.message);
    require(ce_read_file(&arena,"grammar/TheoryModule.cf",&source,&error) &&
        ce_grammar_read(&syntax,source,&error) && ce_grammar_finish(&syntax,&error),error.message);
    CeCatalog catalog = {.syntax=&syntax}; CeModule *module; CePresentation declarations;
    CeViewProfile profile = {0};
    require(ce_module_load(&catalog,cmera ? "presentations/CMeraStyle.module" : compact2 ? "presentations/CompactC2.module" :
            "presentations/CompactC.module",&module,&error) &&
        ce_module_theory(&catalog,module,cmera ? "CMeraStyle" : compact2 ? "CompactC2" : "CompactC",&declarations,&error) &&
        ce_view_compile(&profile,&g,&catalog,&declarations,cmera ? "cmera-style" : "compact-c",compact2 ? 2 : 1,&error),error.message);
    char path[256];
    require(snprintf(path,sizeof(path),"build/%s-rules.json",prefix) < (int)sizeof(path),"rule output path fits");
    FILE *rules = fopen(path,"wb");
    require(rules && ce_view_rules_export(&profile,rules,&error),error.message); fclose(rules);
    require(snprintf(path,sizeof(path),"build/%s-schema.json",prefix) < (int)sizeof(path),"schema output path fits");
    FILE *schema = fopen(path,"wb");
    require(schema && ce_metadata_export(&g,schema,&error),error.message); fclose(schema);
    require(snprintf(path,sizeof(path),"build/%s-cases.json",prefix) < (int)sizeof(path),"case output path fits");
    FILE *cases = fopen(path,"wb");
    FILE *counts = fopen("build/presentation-constructor-coverage.json","wb");
    require(cases && counts,"coverage output files"); fputc('[',cases); fputc('[',counts);
    size_t constructors = 0, covered = 0; uint64_t nodes = 0, shortened = 0;
    for (size_t i = 0; i < g.nproductions; ++i) {
        const CeProduction *p = &g.productions[i];
        if (p->internal || g.symbols[p->lhs].erased ||
            g.symbols[p->lhs].list_element >= 0 || ce_is(p->label,"_")) continue;
        CeArena specimen = {0}; unsigned char *active = ce_alloc(&specimen,g.nsymbols);
        CeTree *tree = constructor(&g,i,&specimen,active), *admitted, *expanded; CeAtom *surface;
        if (!tree) fprintf(stderr,"uninhabited specimen: %.*s\n",(int)p->label.len,p->label.data);
        require(tree != NULL,"every AST constructor has an inhabited structural specimen");
        require(ce_tree_decode_atom(&g,ce_tree_to_atom(&g,tree,&specimen),p->lhs,-1,
            &specimen,NULL,NULL,&admitted,&error,0),error.message);
        require(ce_view_tree(&profile,admitted,p->lhs,&specimen,&surface,&error),error.message);
        CeViewStats stats = {0};
        require(ce_view_expand(&profile,surface,p->lhs,&specimen,&expanded,&stats,&error) &&
            ce_tree_equal(&g,admitted,expanded),error.message);
        if (!stats.short_by_constructor[i]) fprintf(stderr,"no genuine short form: %.*s\n",(int)p->label.len,p->label.data);
        if (constructors) { fputc(',',counts); fputc(',',cases); }
        fputs("{\"constructor\":",counts); ce_quote(counts,p->label);
        fprintf(counts,",\"nodes\":%llu,\"shortened\":%llu}",
            (unsigned long long)stats.constructors,(unsigned long long)stats.shortened);
        fputs("{\"sort\":",cases); ce_quote(cases,g.symbols[g.symbols[p->lhs].sort].name);
        FILE *stream = tmpfile(); require(stream != NULL,"specimen surface stream");
        ce_atom_write(surface,stream); long length = ftell(stream); require(length >= 0,"measure specimen surface");
        source = (CeText){ce_alloc(&specimen,(size_t)length+1),(size_t)length}; rewind(stream);
        require(fread(source.data,1,source.len,stream) == source.len,"read specimen surface"); fclose(stream);
        fputs(",\"surface\":",cases); ce_quote(cases,source);
        stream = tmpfile(); require(stream != NULL,"specimen AST stream");
        ce_tree_encode(&g,admitted,stream); length = ftell(stream); require(length >= 0,"measure specimen AST");
        source = (CeText){ce_alloc(&specimen,(size_t)length+1),(size_t)length}; rewind(stream);
        require(fread(source.data,1,source.len,stream) == source.len,"read specimen AST"); fclose(stream);
        fputs(",\"ast\":",cases); ce_quote(cases,source); fputc('}',cases);
        ++constructors; covered += stats.short_by_constructor[i] > 0;
        nodes += stats.constructors; shortened += stats.shortened;
        ce_view_stats_free(&stats); ce_arena_free(&specimen);
    }
    fputs("]\n",cases); fputs("]\n",counts); fclose(cases); fclose(counts);
    require(covered == constructors,"every original root constructor has a genuine short form");
    require(constructors == 299,"complete supported constructor accounting");
    printf("presentation coverage: %zu/%zu original root constructors shortened; %llu/%llu total specimen constructors; %u checks\n",
        covered,constructors,(unsigned long long)shortened,(unsigned long long)nodes,checks);
    ce_view_free(&profile); ce_catalog_free(&catalog); ce_grammar_free(&syntax);
    ce_grammar_free(&g); ce_arena_free(&arena); return 0;
}
