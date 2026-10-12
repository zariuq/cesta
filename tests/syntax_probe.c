#include "../src/cesta.h"

#include <stdlib.h>
#include <string.h>

/* Keep the actual DFA/LR tables live while an oracle replaces one work file.
   Inventory counts inspect the AST selected by the same parser. */
static void inventory(const CeTree *tree, size_t *counts) {
    if (tree->production >= 0) ++counts[tree->production];
    for (size_t i = 0; i < tree->len; ++i) inventory(tree->children[i],counts);
}
int main(int argc, char **argv) {
    if (argc != 3) { fputs("usage: syntax-probe GRAMMAR.cf PROFILE\n",stderr); return 2; }
    CeArena setup = {0}; CeGrammar g = {0}; CeDFA dfa = {0}; CeLR lr = {0};
    CeError error = {{0},0}; CeText text;
    bool ready = ce_read_file(&setup,argv[1],&text,&error) && ce_grammar_read(&g,text,&error) &&
        ce_grammar_finish(&g,&error) && ce_profile_load(&g,argv[2],&error) &&
        ce_dfa_prepare(&g,&dfa,&error) && ce_lr_prepare(&g,g.entries[0],&lr,&error) && !lr.conflicts;
    if (!ready) { fprintf(stderr,"syntax-probe: %s\n",error.message); return 1; }
    char line[8192];
    while (fgets(line,sizeof(line),stdin)) {
        size_t len = strlen(line);
        if (len < 3 || line[len-1] != '\n' || line[1] != ' ' ||
            (line[0] != 'T' && line[0] != 'P' && line[0] != 'I')) {
            fputs("syntax-probe: invalid request\n",stderr); return 2;
        }
        line[len-1] = 0;
        CeArena a = {0}; CeTokens tokens = {0}; CeForest forest = {.root=-1};
        error = (CeError){{0},0};
        bool accepted = ce_read_file(&a,line+2,&text,&error) && ce_dfa_lex(&g,&dfa,text,&tokens,&error);
        if (line[0] == 'T' && accepted) {
            printf("T %zu",tokens.len);
            for (size_t i = 0; i < tokens.len; ++i)
                printf(" %zu %zu",tokens.data[i].offset,tokens.data[i].text.len);
            fputc('\n',stdout);
        } else {
            if (accepted) accepted = ce_lr_parse(&g,&lr,&tokens,&forest,&error);
            CeTree *tree = NULL;
            if (accepted && line[0] == 'I')
                accepted = ce_tree_from_forest(&g,&forest,&tokens,&a,&tree,&error);
            if (accepted && line[0] == 'I') {
                size_t *counts = calloc(g.nproductions,sizeof(*counts));
                if (!counts) { fputs("syntax-probe: out of memory\n",stderr); return 2; }
                inventory(tree,counts);
                fputs("I {",stdout); bool comma = false;
                for (size_t i = 0; i < g.nproductions; ++i)
                    if (counts[i] && g.productions[i].label.len >= 3 && !memcmp(g.productions[i].label.data,"GNU",3)) {
                        printf("%s\"%.*s\":%zu",comma ? "," : "",(int)g.productions[i].label.len,
                               g.productions[i].label.data,counts[i]); comma = true;
                    }
                fputs("}\n",stdout); free(counts);
            } else printf("%c %zu %s\n",accepted ? 'A' : 'R',error.offset,error.message);
        }
        fflush(stdout); ce_forest_free(&forest); ce_tokens_free(&tokens); ce_arena_free(&a);
    }
    ce_lr_free(&lr); ce_dfa_free(&dfa); ce_grammar_free(&g); ce_arena_free(&setup);
    return ferror(stdin) ? 2 : 0;
}
