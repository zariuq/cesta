#include "../src/cesta.h"

#include <stdlib.h>

static bool matches(CeAction action, CeAction selector) {
    return action.kind == selector.kind &&
        (selector.kind == CE_LR_SHIFT || action.value == selector.value);
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    CeArena arena = {0}; CeGrammar grammar = {0}; CeLR lr = {0};
    CeText source; CeError error = {{0},0};
    if (!ce_read_file(&arena,argv[1],&source,&error) ||
        !ce_grammar_read(&grammar,source,&error) || !ce_grammar_finish(&grammar,&error) ||
        !ce_profile_load(&grammar,argv[2],&error)) goto failed;
    int entry = -1;
    for (size_t i = 0; i < grammar.nsymbols; ++i)
        if (ce_is(grammar.symbols[i].name,"TranslationUnit")) entry = (int)i;
    if (entry < 0 || !ce_lr_prepare(&grammar,entry,&lr,&error) || lr.conflicts) goto failed;
    fputs("[",stdout); bool first = true;
    for (size_t i = 0; i < lr.states*lr.symbols; ++i) {
        const CeActionCell *cell = &lr.cells[i];
        if (cell->len < 2) continue;
        bool conditional = grammar.policies[cell->policy].next != -2;
        for (unsigned branch = 0; branch < (conditional ? 2u : 1u); ++branch) {
            if (!first) fputc(',',stdout);
            first = false;
            fprintf(stdout,"{\"state\":%zu,\"terminal\":%zu,\"guard_matches\":%s,\"selected\":%d,\"alternatives\":[",
                i/lr.symbols,i%lr.symbols,branch ? "false" : "true",
                branch ? 1-cell->selected : cell->selected);
            for (size_t a = 0; a < cell->len; ++a) fprintf(stdout,"%s%zu",a ? "," : "",a);
            fputs("],\"preferences\":[",stdout); bool edge_first = true;
            /* Export every applicable declaration independently of the selected
               action. The kernel checks whether these edges authorize it. */
            for (size_t j = 0; j < grammar.npolicies; ++j) {
                const CeConflictPolicy *p = &grammar.policies[j];
                size_t terminal = p->terminal < 0 ? grammar.nsymbols : (size_t)p->terminal;
                if (p->terminal != -3 && terminal != i%lr.symbols) continue;
                if (cell->len != 2 && p->next != -2) continue;
                for (size_t a = 0; a < cell->len; ++a) for (size_t b = 0; b < cell->len; ++b) {
                    if (a == b || !matches(cell->actions[a],p->preferred) ||
                        !matches(cell->actions[b],p->alternative)) continue;
                    if (!edge_first) fputc(',',stdout);
                    edge_first = false;
                    bool reverse = branch && p->next != -2;
                    fprintf(stdout,"[%zu,%zu]",reverse ? b : a,reverse ? a : b);
                }
            }
            fputs("]}",stdout);
        }
    }
    puts("]");
    ce_lr_free(&lr); ce_grammar_free(&grammar); ce_arena_free(&arena);
    return 0;
failed:
    fprintf(stderr,"conflict certificate: %s\n",error.message);
    ce_lr_free(&lr); ce_grammar_free(&grammar); ce_arena_free(&arena);
    return 1;
}
