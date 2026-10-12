#include "../src/cesta.h"

#include <limits.h>
#include <stdlib.h>
#include <string.h>

/* Grammar derivations of scalar expressions are inserted into a declaration
   context binding both identifier choices. The ordered RHS and precedence
   categories come from the loaded grammar, including transparent productions. */
static uint64_t random_state = UINT64_C(0x4345535441534558);
static unsigned *height;
static unsigned *minimum;
static size_t *used;

static unsigned random_number(void) {
    random_state ^= random_state << 13;
    random_state ^= random_state >> 7;
    random_state ^= random_state << 17;
    return (unsigned)(random_state >> 16);
}

static void require(bool valid, const char *message) {
    if (!valid) { fprintf(stderr,"grammar-tree qualification: %s\n",message); exit(1); }
}

static bool expression_rule(const CeProduction *p) {
    const char *labels[] = {"_","Var","IntConst","UnaryPlus","Negate","BitNot","Not",
        "Multiply","Divide","Remainder","Add","Subtract","ShiftLeft","ShiftRight",
        "Less","Greater","LessEqual","GreaterEqual","Equal","NotEqual","BitAnd",
        "BitXor","BitOr","And","Or","Conditional","Comma"};
    for (size_t i = 0; i < sizeof(labels)/sizeof(*labels); ++i)
        if (ce_is(p->label,labels[i])) return true;
    return false;
}

static unsigned production_height(const CeGrammar *g, const CeProduction *p) {
    unsigned maximum = 0;
    for (size_t i = 0; i < p->len; ++i) {
        unsigned h = height[p->rhs[i]];
        if (h == UINT_MAX) return h;
        if (h > maximum) maximum = h;
    }
    (void)g; return maximum+1;
}

static unsigned production_size(const CeGrammar *g, const CeProduction *p) {
    unsigned size = 1;
    for (size_t i = 0; i < p->len; ++i) {
        const CeSymbol *s = &g->symbols[p->rhs[i]];
        if (s->kind == CE_LITERAL || s->erased) continue;
        unsigned child = minimum[p->rhs[i]];
        if (child == UINT_MAX || child > UINT_MAX-size) return UINT_MAX;
        size += child;
    }
    return size;
}

static bool identifier(const CeGrammar *g, int symbol) {
    return g->name_token >= 0 &&
        g->symbols[symbol].sort == g->symbols[g->name_token].sort;
}

static CeTree *node(CeArena *a, int symbol, int production, size_t length) {
    CeTree *tree = ce_alloc(a,sizeof(*tree));
    tree->symbol = symbol; tree->production = production; tree->len = length;
    tree->children = ce_alloc(a,length*sizeof(*tree->children)); return tree;
}

static CeTree *derive(const CeGrammar *g, int symbol, unsigned depth,
                      unsigned *budget, CeArena *a) {
    if (!*budget || minimum[symbol] > *budget) return NULL;
    const CeSymbol *s = &g->symbols[symbol];
    if (s->kind == CE_TOKEN) {
        --*budget;
        CeTree *t = node(a,symbol,-1,0);
        if (identifier(g,symbol)) t->spelling = ce_cstr(a,random_number()%2 ? "x" : "y");
        else if (ce_is(s->name,"CInteger")) {
            char number[40]; snprintf(number,sizeof(number),"%uu",random_number()%1000000);
            t->spelling = ce_cstr(a,number);
        } else return NULL;
        return t;
    }
    if (!depth) return NULL;
    size_t eligible = 0;
    for (size_t i = 0; i < g->nproductions; ++i)
        if (g->productions[i].lhs == symbol && expression_rule(&g->productions[i]) &&
            production_height(g,&g->productions[i]) <= depth &&
            production_size(g,&g->productions[i]) <= *budget) ++eligible;
    if (!eligible) return NULL;
    size_t choice = random_number()%eligible; int selected = -1;
    for (size_t i = 0; i < g->nproductions; ++i)
        if (g->productions[i].lhs == symbol && expression_rule(&g->productions[i]) &&
            production_height(g,&g->productions[i]) <= depth &&
            production_size(g,&g->productions[i]) <= *budget) {
            if (!choice--) { selected = (int)i; break; }
        }
    require(selected >= 0,"choose an actual source production");
    const CeProduction *p = &g->productions[selected]; --*budget;
    CeTree **children = ce_alloc(a,p->len*sizeof(*children)); size_t count = 0;
    for (size_t i = 0; i < p->len; ++i) {
        int child = p->rhs[i];
        if (g->symbols[child].kind == CE_LITERAL || g->symbols[child].erased) continue;
        unsigned reserved = 0;
        for (size_t j = i+1; j < p->len; ++j) {
            const CeSymbol *later = &g->symbols[p->rhs[j]];
            if (later->kind != CE_LITERAL && !later->erased) reserved += minimum[p->rhs[j]];
        }
        require(reserved <= *budget,"reserve remaining ordered grammar arguments");
        unsigned allowance = *budget-reserved;
        children[count] = derive(g,child,depth-1,&allowance,a);
        *budget = allowance+reserved;
        if (!children[count++]) return NULL;
    }
    if (ce_is(p->label,"_")) {
        require(count == 1,"transparent source production has one visible argument");
        return children[0];
    }
    CeTree *tree = node(a,symbol,selected,count);
    if (count) memcpy(tree->children,children,count*sizeof(*children));
    return tree;
}

static void record(const CeTree *tree) {
    if (tree->production >= 0) ++used[tree->production];
    for (size_t i = 0; i < tree->len; ++i) record(tree->children[i]);
}

static CeTree *find(const CeGrammar *g, CeTree *tree, const char *label) {
    if (ce_tree_label(g,tree,label)) return tree;
    for (size_t i = 0; i < tree->len; ++i) {
        CeTree *found = find(g,tree->children[i],label); if (found) return found;
    }
    return NULL;
}

static CeText contents(CeArena *a, FILE *file) {
    require(fflush(file) == 0 && fseek(file,0,SEEK_END) == 0,"seek qualification stream");
    long size = ftell(file);
    require(size >= 0 && fseek(file,0,SEEK_SET) == 0,"rewind qualification stream");
    CeText text = {ce_alloc(a,(size_t)size+1),(size_t)size};
    require(fread(text.data,1,text.len,file) == text.len,"read qualification stream"); return text;
}

static CeText print(const CeGrammar *g, const CeDFA *lexer, CeTree *tree, int entry, CeArena *a, bool wire, CeError *error) {
    FILE *file = tmpfile(); require(file != NULL,"open qualification stream");
    if (wire) ce_tree_encode(g,tree,file);
    else require(ce_tree_print_prepared(g,lexer,tree,entry,file,error),error->message);
    CeText text = contents(a,file); fclose(file); return text;
}

static CeTree *parse(const CeGrammar *g, const CeDFA *dfa, const CeLR *lr,
                     CeText text, CeArena *a, CeError *error) {
    CeTokens tokens = {0}; CeForest forest = {.root=-1}; CeTree *tree;
    require(ce_dfa_lex(g,dfa,text,&tokens,error),error->message);
    require(ce_lr_parse(g,lr,&tokens,&forest,error),error->message);
    require(ce_tree_from_forest(g,&forest,&tokens,a,&tree,error),error->message);
    ce_forest_free(&forest); ce_tokens_free(&tokens); return tree;
}

int main(int argc, char **argv) {
    require(argc == 4,"arguments are GRAMMAR PROFILE TREE-COUNT");
    size_t wanted = (size_t)strtoul(argv[3],NULL,10);
    CeArena setup = {0}; CeGrammar g = {0}; CeDFA dfa = {0}; CeLR lr = {0}; CeError error = {{0},0}; CeText source;
    require(ce_read_file(&setup,argv[1],&source,&error) && ce_grammar_read(&g,source,&error) &&
            ce_grammar_finish(&g,&error) && ce_profile_load(&g,argv[2],&error),error.message);
    int entry = g.entries[0];
    require(ce_dfa_prepare(&g,&dfa,&error) && ce_lr_prepare(&g,entry,&lr,&error),error.message);
    require(lr.conflicts == 0,"no undeclared parser conflicts");
    CeTree *context = parse(&g,&dfa,&lr,ce_cstr(&setup,"unsigned f(unsigned x,unsigned y){return 0u;}"),&setup,&error);
    CeTree *full = find(&g,context,"SomeFullExpression");
    require(full && full->len == 1,"one generated expression in a bound parameter context");
    int expr = g.productions[full->production].rhs[0]; CeTree *saved = full->children[0];
    height = ce_resize(NULL,g.nsymbols,sizeof(*height));
    minimum = ce_resize(NULL,g.nsymbols,sizeof(*minimum));
    used = calloc(g.nproductions,sizeof(*used));
    require(used != NULL,"allocate source-coverage counters");
    for (size_t i = 0; i < g.nsymbols; ++i) {
        bool fixed = g.symbols[i].kind == CE_LITERAL || g.symbols[i].erased;
        bool token = g.symbols[i].kind == CE_TOKEN &&
            (identifier(&g,(int)i) || ce_is(g.symbols[i].name,"CInteger"));
        height[i] = fixed || token ? 0 : UINT_MAX;
        minimum[i] = fixed ? 0 : token ? 1 : UINT_MAX;
    }
    bool changed;
    do {
        changed = false;
        for (size_t i = 0; i < g.nproductions; ++i) if (expression_rule(&g.productions[i])) {
            unsigned cost = production_height(&g,&g.productions[i]); int result = g.productions[i].lhs;
            if (cost < height[result]) { height[result] = cost; changed = true; }
            cost = production_size(&g,&g.productions[i]);
            if (cost < minimum[result]) { minimum[result] = cost; changed = true; }
        }
    } while (changed);
    require(height[expr] != UINT_MAX,"source grammar derives the expression family");
    size_t accepted = 0, attempts = 0;
    while (accepted < wanted) {
        require(attempts++ < wanted*20+100,"bounded grammar derivation retries");
        CeArena a = {0}; unsigned budget = 300;
        CeTree *expression = derive(&g,expr,height[expr]+5+random_number()%20,&budget,&a);
        if (!expression) { ce_arena_free(&a); continue; }
        full->children[0] = expression;
        CeText wire = print(&g,&dfa,context,entry,&a,true,&error); CeTree *decoded;
        require(ce_tree_decode(&g,wire,entry,&a,&decoded,&error) && ce_tree_equal(&g,context,decoded),"decode(encode(tree)) = tree");
        CeText printed = print(&g,&dfa,context,entry,&a,false,&error);
        CeTree *reparsed = parse(&g,&dfa,&lr,printed,&a,&error);
        require(ce_tree_equal(&g,context,reparsed),"parse(print(tree)) = tree");
        require(ce_equal(printed,print(&g,&dfa,reparsed,entry,&a,false,&error)),"print(parse(source)) is idempotent");
        record(expression);
        full->children[0] = saved; ce_arena_free(&a); ++accepted;
    }
    size_t productions = 0;
    for (size_t i = 0; i < g.nproductions; ++i) productions += used[i] != 0;
    printf("grammar-generated bound trees: %zu; attempts: %zu; source productions visited: %zu; all three codec laws passed\n",accepted,attempts,productions);
    free(height); free(minimum); free(used); ce_lr_free(&lr); ce_dfa_free(&dfa); ce_grammar_free(&g); ce_arena_free(&setup); return 0;
}
