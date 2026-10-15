#include "../src/cesta.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

/* Source productions choose declarations, statements and types as well as
   expressions. A forced-production pass accounts for every constructor;
   rejected derivations never count as successful round-trip samples. */
static uint64_t random_state = UINT64_C(0x57484f4c45434553);
static unsigned *minimum, *forced;
static uint64_t *used;
static bool *sorts_used;
static unsigned fresh_name;
static unsigned next_number(void) {
    random_state ^= random_state << 13; random_state ^= random_state >> 7; random_state ^= random_state << 17;
    return (unsigned)(random_state >> 16);
}
static void require(bool ok, const char *message) {
    if (!ok) { fprintf(stderr,"whole-program trees: %s\n",message); exit(1); }
}
static bool visible(const CeGrammar *g, int symbol) {
    return g->symbols[symbol].kind != CE_LITERAL && !g->symbols[symbol].erased;
}
static unsigned cost(const CeGrammar *g, const CeProduction *p, int forced_child) {
    unsigned n = 1;
    for (size_t i = 0; i < p->len; ++i) if (visible(g,p->rhs[i])) {
        unsigned child = (int)i == forced_child ? forced[p->rhs[i]] : minimum[p->rhs[i]];
        if (child == UINT_MAX || child > UINT_MAX-n) return UINT_MAX;
        n += child;
    }
    return n;
}
static void prepare(const CeGrammar *g, int target) {
    for (size_t i = 0; i < g->nsymbols; ++i) {
        minimum[i] = g->symbols[i].kind == CE_TOKEN ? 1 : visible(g,(int)i) ? UINT_MAX : 0;
        forced[i] = UINT_MAX;
    }
    bool changed;
    do {
        changed = false;
        for (size_t i = 0; i < g->nproductions; ++i) {
            const CeProduction *p = &g->productions[i]; unsigned n = cost(g,p,-1);
            if (n < minimum[p->lhs]) { minimum[p->lhs] = n; changed = true; }
        }
    } while (changed);
    if (target < 0) return;
    const CeProduction *wanted = &g->productions[target]; forced[wanted->lhs] = cost(g,wanted,-1);
    do {
        changed = false;
        for (size_t i = 0; i < g->nproductions; ++i) {
            const CeProduction *p = &g->productions[i];
            for (size_t j = 0; j < p->len; ++j) if (visible(g,p->rhs[j])) {
                unsigned n = cost(g,p,(int)j);
                if (n < forced[p->lhs]) { forced[p->lhs] = n; changed = true; }
            }
        }
    } while (changed);
}
static CeTree *node(CeArena *a, int symbol, int production, size_t len) {
    CeTree *t = ce_alloc(a,sizeof(*t)); t->symbol = symbol; t->production = production; t->len = len;
    t->children = ce_alloc(a,len*sizeof(*t->children)); return t;
}
static CeTree *clone(const CeTree *source, CeArena *a) {
    CeTree *tree = node(a,source->symbol,source->production,source->len); tree->spelling = source->spelling;
    for (size_t i = 0; i < tree->len; ++i) tree->children[i] = clone(source->children[i],a);
    return tree;
}
static CeTree *derive(const CeGrammar *g, int symbol, int target, int parent,
                       unsigned *budget, unsigned depth, CeArena *a) {
    if (!depth || !*budget) return NULL;
    const CeSymbol *s = &g->symbols[symbol];
    if (s->kind == CE_TOKEN) {
        --*budget;
        CeTree *t = node(a,symbol,-1,0); const char *text = NULL;
        const CeProduction *p = parent >= 0 ? &g->productions[parent] : NULL;
        if (ce_token_symbol_representation(g,symbol)) {
            if (p && ce_is(p->label,"TypedefType")) text = "T";
            else if (p && ce_is(p->label,"EnumReference")) text = "E";
            else if (p && ce_is(p->label,"StructReference")) text = "S";
            else if (p && ce_is(p->label,"GNUNamedAttribute")) text = "unused";
            else if (p && (ce_is(p->label,"NameDeclarator") || ce_is(p->label,"SomeIdent") ||
                ce_is(p->label,"Enumerator") || ce_is(p->label,"EnumeratorValue") || ce_is(p->label,"Label"))) {
                char name[40]; snprintf(name,sizeof(name),"fresh%u",fresh_name++); t->spelling = ce_cstr(a,name); return t;
            } else text = "x";
        } else if (ce_is(s->name,"CInteger")) {
            char value[40]; snprintf(value,sizeof(value),"%uu",1+next_number()%127); t->spelling = ce_cstr(a,value); return t;
        } else if (ce_is(s->name,"CFloat")) text = "1.0";
        else if (ce_is(s->name,"CCharacter")) text = "'a'";
        else if (ce_is(s->name,"CString")) text = "\"s\"";
        else if (ce_is(s->name,"CPragma")) text = "#pragma GCC diagnostic push";
        else if (ce_is(s->name,"CIdentDirective")) text = "#ident \"tree\"";
        if (!text) return NULL;
        t->spelling = ce_cstr(a,text); return t;
    }
    if (s->list_element >= 0) {
        bool empty = false;
        for (size_t i = 0; i < g->nproductions; ++i) empty |= g->productions[i].lhs == symbol && ce_is(g->productions[i].label,"[]");
        size_t len = target >= 0 || !empty ? 1 : next_number()%5 ? 0 : 1;
        if (len && depth > 10 && *budget > 30 && next_number()%8 == 0) ++len;
        --*budget;
        CeTree *t = node(a,symbol,-2,len);
        for (size_t i = 0; i < len; ++i) {
            unsigned reserved = (unsigned)(len-i-1)*minimum[s->list_element];
            if (reserved >= *budget) return NULL;
            unsigned available = *budget-reserved;
            t->children[i] = derive(g,s->list_element,i ? -1 : target,-1,&available,depth-1,a);
            *budget = available+reserved;
            if (!t->children[i]) return NULL;
        }
        return t;
    }
    int selected = -1, path = -1;
    unsigned eligible = 0;
    for (size_t i = 0; i < g->nproductions; ++i) {
        const CeProduction *p = &g->productions[i]; if (p->lhs != symbol) continue;
        int via = -1; unsigned n = cost(g,p,-1);
        if (target >= 0 && (int)i != target) {
            n = UINT_MAX;
            for (size_t j = 0; j < p->len; ++j) if (visible(g,p->rhs[j])) {
                unsigned trial = cost(g,p,(int)j);
                if (trial < n) { n = trial; via = (int)j; }
            }
        }
        unsigned best = target >= 0 ? forced[symbol] : minimum[symbol];
        if (n > *budget || n == UINT_MAX || (depth < 15 && n > best+5)) continue;
        if (!eligible++ || next_number()%eligible == 0) { selected = (int)i; path = via; }
    }
    if (selected < 0) return NULL;
    --*budget;
    const CeProduction *p = &g->productions[selected];
    size_t nchildren = 0;
    for (size_t i = 0; i < p->len; ++i) nchildren += visible(g,p->rhs[i]);
    CeTree *t = node(a,symbol,selected,nchildren); size_t n = 0;
    for (size_t i = 0; i < p->len; ++i) if (visible(g,p->rhs[i])) {
        unsigned reserved = 0;
        for (size_t j = i+1; j < p->len; ++j) if (visible(g,p->rhs[j])) {
            unsigned child = (int)j == path ? forced[p->rhs[j]] : minimum[p->rhs[j]];
            if (child > UINT_MAX-reserved) return NULL;
            reserved += child;
        }
        if (reserved >= *budget) return NULL;
        unsigned available = *budget-reserved;
        t->children[n] = derive(g,p->rhs[i],(int)i == path ? target : -1,
            ce_is(p->label,"_") ? parent : selected,&available,depth-1,a);
        *budget = available+reserved;
        if (!t->children[n++]) return NULL;
    }
    if (ce_is(p->label,"_")) return n == 1 ? t->children[0] : NULL;
    return t;
}
static bool parse(const CeGrammar *g, const CeDFA *dfa, const CeLR *lr,
                   CeText source, CeArena *a, CeTree **out, CeError *error) {
    CeTokens tokens = {0}; CeForest forest = {.root=-1};
    bool ok = ce_dfa_lex(g,dfa,source,&tokens,error) && ce_lr_parse(g,lr,&tokens,&forest,error) &&
        ce_tree_from_forest(g,&forest,&tokens,a,out,error);
    ce_forest_free(&forest); ce_tokens_free(&tokens); return ok;
}
static CeText print(const CeGrammar *g, const CeDFA *lexer, CeTree *tree, int sort, CeArena *a, CeError *error) {
    FILE *file = tmpfile(); require(file != NULL,"printer stream");
    if (!ce_tree_print_prepared(g,lexer,tree,sort,file,error)) { fclose(file); return (CeText){NULL,0}; }
    long size = ftell(file); require(size >= 0,"printer size"); rewind(file);
    CeText result = {ce_alloc(a,(size_t)size+1),(size_t)size};
    require(fread(result.data,1,result.len,file) == result.len,"printer output"); fclose(file); return result;
}
static CeText encode(const CeGrammar *g, const CeTree *tree, CeArena *a) {
    FILE *file = tmpfile(); require(file != NULL,"AST identity stream"); ce_tree_encode(g,tree,file);
    long size = ftell(file); require(size >= 0,"AST identity size"); rewind(file);
    CeText result = {ce_alloc(a,(size_t)size+1),(size_t)size};
    require(fread(result.data,1,result.len,file) == result.len,"AST identity bytes"); fclose(file); return result;
}
static void record(const CeTree *tree) {
    if (tree->production >= 0) ++used[tree->production];
    for (size_t i = 0; i < tree->len; ++i) record(tree->children[i]);
}
static void record_sorts(const CeGrammar *g, const CeTree *tree) {
    sorts_used[g->symbols[tree->symbol].sort] = true;
    for (size_t i = 0; i < tree->len; ++i) record_sorts(g,tree->children[i]);
}
static void source_sorts(const CeGrammar *g, int symbol, bool *seen, bool *reachable) {
    if (seen[symbol] || g->symbols[symbol].erased) return;
    seen[symbol] = true; reachable[g->symbols[symbol].sort] = true;
    for (size_t i = 0; i < g->nproductions; ++i) if (g->productions[i].lhs == symbol)
        for (size_t j = 0; j < g->productions[i].len; ++j)
            source_sorts(g,g->productions[i].rhs[j],seen,reachable);
}
static bool has(const CeGrammar *g, const CeTree *tree, const char *label) {
    if (ce_tree_label(g,tree,label)) return true;
    for (size_t i = 0; i < tree->len; ++i) if (has(g,tree->children[i],label)) return true;
    return false;
}
static void labels(const CeGrammar *g, CeTree *tree, CeTree *label, CeArena *a) {
    if (ce_tree_label(g,tree,"FunctionDefinition")) {
        CeTree *body = tree->children[3]->children[0];
        CeTree **items = ce_alloc(a,(body->len+1)*sizeof(*items)); items[0] = label;
        if (body->len) memcpy(items+1,body->children,body->len*sizeof(*items));
        body->children = items; ++body->len;
    }
    for (size_t i = 0; i < tree->len; ++i) labels(g,tree->children[i],label,a);
}
static bool closed(const CeGrammar *g, const CeTree *tree, bool in_function) {
    if (ce_tree_label(g,tree,"Var")) return tree->len == 1 && ce_is(tree->children[0]->spelling,"x");
    if (ce_tree_label(g,tree,"TypedefType")) return tree->len == 1 && ce_is(tree->children[0]->spelling,"T");
    if ((ce_tree_label(g,tree,"Goto") || ce_tree_label(g,tree,"GNULabelAddress") ||
         ce_tree_label(g,tree,"GNUAsmLabel") || ce_tree_label(g,tree,"Label")) && !in_function) return false;
    if (ce_tree_label(g,tree,"FunctionDefinition")) {
        for (size_t i = 0; i < tree->len; ++i)
            if (!closed(g,tree->children[i],i == 3)) return false;
        return true;
    }
    for (size_t i = 0; i < tree->len; ++i) if (!closed(g,tree->children[i],in_function)) return false;
    return true;
}
int main(int argc, char **argv) {
    require(argc == 2,"argument is positive whole-program count");
    size_t wanted = strtoul(argv[1],NULL,10); require(wanted > 0,"positive sample count");
    CeArena setup = {0}; CeGrammar g = {0}, syntax = {0}; CeDFA dfa = {0}; CeLR lr = {0}; CeError error = {{0},0}; CeText source;
    require(ce_read_file(&setup,"build/CGNU.cf",&source,&error) && ce_grammar_read(&g,source,&error) &&
        ce_grammar_finish(&g,&error) && ce_profile_load(&g,"grammar/CGNU.profile",&error),error.message);
    int entry = g.entries[0];
    require(ce_dfa_prepare(&g,&dfa,&error) && ce_lr_prepare(&g,entry,&lr,&error),error.message);
    require(ce_read_file(&setup,"grammar/TheoryModule.cf",&source,&error) && ce_grammar_read(&syntax,source,&error) &&
        ce_grammar_finish(&syntax,&error),error.message);
    CeCatalog catalog = {.syntax=&syntax}; CeViewProfile profiles[3]; memset(profiles,0,sizeof(profiles));
    const char *modules[] = {"presentations/CompactC.module","presentations/CompactC2.module","presentations/CMeraStyle.module"};
    const char *theories[] = {"CompactC","CompactC2","CMeraStyle"}, *names[] = {"compact-c","compact-c","cmera-style"};
    for (size_t i = 0; i < 3; ++i) {
        CeModule *module; CePresentation theory;
        require(ce_module_load(&catalog,modules[i],&module,&error) && ce_module_theory(&catalog,module,theories[i],&theory,&error) &&
            ce_view_compile(&profiles[i],&g,&catalog,&theory,names[i],i == 1 ? 2 : 1,&error),error.message);
    }
    CeTree *prelude, *label_context, *function_context;
    require(parse(&g,&dfa,&lr,ce_cstr(&setup,"typedef unsigned T;unsigned x;enum E{enumerator=1};struct S{unsigned x;};"),&setup,&prelude,&error),error.message);
    require(parse(&g,&dfa,&lr,ce_cstr(&setup,"void label_context(void){x:;}"),&setup,&label_context,&error),error.message);
    require(parse(&g,&dfa,&lr,ce_cstr(&setup,"void generated_context(void){;}"),&setup,&function_context,&error),error.message);
    CeTree *label = label_context->children[0]->children[0]->children[0]->children[3]->children[0]->children[0];
    require(has(&g,label,"Label"),"label scaffold");
    minimum = calloc(g.nsymbols,sizeof(*minimum)); forced = calloc(g.nsymbols,sizeof(*forced)); used = calloc(g.nproductions,sizeof(*used));
    sorts_used = calloc(g.nsymbols,sizeof(*sorts_used));
    require(minimum && forced && used && sorts_used,"coverage state");
    CeText *identities = NULL; uint64_t *identity_hashes = NULL;
    size_t accepted = 0, attempts = 0, reachable = 0;
    int statement = -1;
    for (size_t i = 0; i < g.nsymbols; ++i) if (ce_is(g.symbols[i].name,"Statement")) statement = (int)i;
    require(statement >= 0,"statement category exists");
    size_t total_targets = 0;
    for (size_t i = 0; i < g.nproductions; ++i) {
        const CeProduction *p = &g.productions[i];
        total_targets += !p->internal && !g.symbols[p->lhs].erased && g.symbols[p->lhs].list_element < 0 && !ce_is(p->label,"_");
    }
    size_t per_target = (wanted+2*total_targets-1)/total_targets;
    for (size_t target = 0; target < g.nproductions; ++target) {
        const CeProduction *p = &g.productions[target];
        if (p->internal || g.symbols[p->lhs].erased || g.symbols[p->lhs].list_element >= 0 || ce_is(p->label,"_")) continue;
        prepare(&g,(int)target); require(forced[entry] != UINT_MAX,"constructor reachable from the source entry"); ++reachable;
        printf("target %zu/%zu %.*s\n",reachable,total_targets,(int)p->label.len,p->label.data); fflush(stdout);
        size_t trials = 0;
        size_t target_count = 0;
        size_t target_quota = p->lhs == entry && p->len == 0 ? 1 : per_target;
        do {
            if (trials++ >= 3000) {
                fprintf(stderr,"target %.*s: %s\n",(int)p->label.len,p->label.data,error.message);
                require(false,"bounded constructor-directed derivation retries");
            }
            ++attempts;
            bool body_context = forced[statement] != UINT_MAX;
            int derive_entry = body_context ? statement : entry;
            unsigned budget = forced[derive_entry]+50+next_number()%150;
            CeArena a = {0}; CeTree *tree = derive(&g,derive_entry,(int)target,-1,&budget,80,&a);
            if (!tree) { ce_arena_free(&a); continue; }
            if (body_context) {
                CeTree *context = clone(function_context,&a);
                context->children[0]->children[0]->children[0]->children[3]->children[0]->children[0]->children[0] = tree;
                tree = context;
            }
            if (tree->len) {
                CeTree *list = tree->children[0], *seed = prelude->children[0];
                char declaration[96];
                snprintf(declaration,sizeof(declaration),"unsigned specimen_%zu = %zuu;",attempts,attempts);
                CeTree *witness;
                require(parse(&g,&dfa,&lr,ce_cstr(&a,declaration),&a,&witness,&error),error.message);
                CeTree **items = ce_alloc(&a,(list->len+seed->len+1)*sizeof(*items));
                memcpy(items,seed->children,seed->len*sizeof(*items));
                items[seed->len] = witness->children[0]->children[0];
                memcpy(items+seed->len+1,list->children,list->len*sizeof(*items));
                list->children = items; list->len += seed->len+1; labels(&g,tree,label,&a);
            }
            if (!closed(&g,tree,false)) { ce_arena_free(&a); continue; }
            CeText printed = print(&g,&dfa,tree,entry,&a,&error); CeTree *parsed;
            if (!printed.data || !parse(&g,&dfa,&lr,printed,&a,&parsed,&error) || !ce_tree_equal(&g,tree,parsed)) {
                if (trials == 3000 && printed.data) {
                    FILE *failed = fopen("build/whole-program-failure.c","wb");
                    require(failed != NULL,"retain rejected constructor specimen");
                    fwrite(printed.data,1,printed.len,failed); fclose(failed);
                }
                ce_arena_free(&a); continue;
            }
            CeText identity = encode(&g,tree,&a); uint64_t hash = UINT64_C(14695981039346656037); bool duplicate = false;
            for (size_t i = 0; i < identity.len; ++i) hash = (hash^identity.data[i])*UINT64_C(1099511628211);
            for (size_t i = 0; i < accepted; ++i) if (identity_hashes[i] == hash && ce_equal(identities[i],identity)) { duplicate = true; break; }
            if (duplicate) { ce_arena_free(&a); continue; }
            CeTree *decoded;
            require(ce_tree_decode_atom(&g,ce_tree_to_atom(&g,tree,&a),entry,-1,&a,NULL,NULL,&decoded,&error,0) &&
                ce_tree_equal(&g,tree,decoded),error.message);
            for (size_t i = 0; i < 3; ++i) {
                CeAtom *surface; CeTree *expanded;
                require(ce_view_tree(&profiles[i],tree,entry,&a,&surface,&error) &&
                    ce_view_expand(&profiles[i],surface,entry,&a,&expanded,NULL,&error) && ce_tree_equal(&g,tree,expanded),error.message);
            }
            CeText normalized = print(&g,&dfa,parsed,entry,&a,&error);
            require(ce_equal(printed,normalized),"source printer idempotence");
            identities = ce_resize(identities,accepted+1,sizeof(*identities)); identity_hashes = ce_resize(identity_hashes,accepted+1,sizeof(*identity_hashes));
            identities[accepted] = ce_text(&setup,identity.data,identity.len); identity_hashes[accepted] = hash;
            record(tree); record_sorts(&g,tree); ++accepted; ++target_count; ce_arena_free(&a);
            if (accepted%1000 == 0) printf("whole-program trees: %zu accepted; %.*s target\n",accepted,(int)p->label.len,p->label.data);
        } while (!used[target] || target_count < target_quota);
    }
    FILE *report = fopen("build/whole-program-tree-coverage.json","wb"); require(report != NULL,"coverage report");
    fprintf(report,"{\"accepted\":%zu,\"distinct_trees\":%zu,\"attempts\":%zu,\"constructors\":[",accepted,accepted,attempts); bool first = true; size_t covered = 0;
    for (size_t i = 0; i < g.nproductions; ++i) {
        const CeProduction *p = &g.productions[i];
        if (p->internal || g.symbols[p->lhs].erased || g.symbols[p->lhs].list_element >= 0 || ce_is(p->label,"_")) continue;
        if (!first) fputc(',',report);
        first = false;
        fputs("{\"name\":",report); ce_quote(report,p->label); fprintf(report,",\"occurrences\":%llu}",(unsigned long long)used[i]); covered += used[i] != 0;
    }
    bool *reachable_sorts = calloc(g.nsymbols,sizeof(*reachable_sorts)), *seen = calloc(g.nsymbols,sizeof(*seen));
    require(reachable_sorts && seen,"source sort reachability"); source_sorts(&g,entry,seen,reachable_sorts);
    size_t sort_count = 0, sort_covered = 0;
    fputs("],\"sorts\":[",report); first = true;
    for (size_t i = 0; i < g.nsymbols; ++i) if (g.symbols[i].sort == (int)i &&
        g.symbols[i].kind != CE_LITERAL && !g.symbols[i].erased) {
        if (!first) fputc(',',report);
        first = false; fputs("{\"name\":",report); ce_quote(report,g.symbols[i].name);
        fprintf(report,",\"reachable\":%s,\"covered\":%s}",reachable_sorts[i] ? "true" : "false",sorts_used[i] ? "true" : "false");
        sort_count += reachable_sorts[i]; sort_covered += reachable_sorts[i] && sorts_used[i];
    }
    fputs("]}\n",report); fclose(report);
    printf("whole-program trees: %zu accepted / %zu attempts; %zu/%zu constructors\n",accepted,attempts,covered,reachable);
    printf("whole-program trees: %zu/%zu reachable AST sorts; scanner-only guards retained separately\n",sort_covered,sort_count);
    require(accepted >= wanted && covered == reachable && sort_covered == sort_count,"full whole-program coverage");
    for (size_t i = 0; i < 3; ++i) ce_view_free(&profiles[i]);
    free(minimum); free(forced); free(used); free(sorts_used); free(identities); free(identity_hashes); free(reachable_sorts); free(seen);
    ce_lr_free(&lr); ce_dfa_free(&dfa); ce_catalog_free(&catalog);
    ce_grammar_free(&syntax); ce_grammar_free(&g); ce_arena_free(&setup); return 0;
}
