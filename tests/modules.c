#include "../src/cesta.h"

#include <stdlib.h>

static unsigned checks;
static void require(bool value, const char *message) {
    ++checks;
    if (!value) { fprintf(stderr,"FAIL: %s\n",message); exit(1); }
}

static void fixture(CeCatalog *catalog, const char *path, bool expected) {
    CeModule *module; CeError error = {{0},0};
    bool accepted = ce_module_load(catalog,path,&module,&error) && ce_module_check(catalog,module,&error);
    require(accepted == expected,path);
    if (!expected) require(error.message[0] != 0,"module refusal identifies admission error");
}

static void parser_parity(const CeGrammar *g, const char *path) {
    CeArena arena = {0}; CeError error = {{0},0}; CeText source;
    CeTokens tokens = {0}; CeForest general = {.root=-1}, deterministic = {.root=-1};
    CeLR lr = {0}; int entry = -1; CeTree *first, *second;
    for (size_t i = 0; i < g->nsymbols; ++i)
        if (g->symbols[i].kind == CE_NONTERMINAL && ce_is(g->symbols[i].name,"Module")) entry = (int)i;
    require(entry >= 0 && ce_lr_prepare(g,entry,&lr,&error) && !lr.ambiguities,
            "shared module grammar has a conflict-free LR realization");
    require(ce_read_file(&arena,path,&source,&error) && ce_lex(g,source,&tokens,&error),error.message);
    require(ce_parse(g,&tokens,entry,&general,&error) &&
        ce_tree_from_forest(g,&general,&tokens,&arena,&first,&error),error.message);
    require(ce_lr_parse(g,&lr,&tokens,&deterministic,&error) &&
        ce_tree_from_forest(g,&deterministic,&tokens,&arena,&second,&error),error.message);
    require(ce_tree_equal(g,first,second),"both module parser engines produce the identical source tree");
    ce_lr_free(&lr); ce_forest_free(&general); ce_forest_free(&deterministic);
    ce_tokens_free(&tokens); ce_arena_free(&arena);
}

int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) { fputs("usage: test-modules MODULE-SYNTAX.cf [REFERENCE-FIXTURES]\n",stderr); return 2; }
    CeArena arena = {0}; CeGrammar syntax = {0}; CeError error = {{0},0}; CeText text;
    require(ce_read_file(&arena,argv[1],&text,&error),error.message);
    require(ce_grammar_read(&syntax,text,&error) && ce_grammar_finish(&syntax,&error),error.message);
    if (argc == 2) {
        parser_parity(&syntax,"tests/modules/LawComposition.module");
        parser_parity(&syntax,"presentations/CompactC.module");
    }
    if (argc == 3)
        require(ce_read_file(&arena,"grammar/metta-venus.profile",&text,&error) && ce_profile_read(&syntax,text,&error),error.message);
    const char *good[] = {"ArithmeticOperations","ArrowCats","List","RenameRewrite","Rholang","UnivAlg"};
    const char *bad[] = {"RepeatLabel","ReplacementShadows"}; char path[1024];
    for (size_t i = 0; argc == 3 && i < sizeof(good)/sizeof(*good); ++i) {
        CeCatalog catalog = {.syntax=&syntax};
        require(snprintf(path,sizeof(path),"%s/%s.module",argv[2],good[i]) < (int)sizeof(path),"fixture path fits");
        fixture(&catalog,path,true); ce_catalog_free(&catalog);
    }
    for (size_t i = 0; argc == 3 && i < sizeof(bad)/sizeof(*bad); ++i) {
        CeCatalog catalog = {.syntax=&syntax};
        require(snprintf(path,sizeof(path),"%s/bad/%s.module",argv[2],bad[i]) < (int)sizeof(path),"negative fixture path fits");
        fixture(&catalog,path,false); ce_catalog_free(&catalog);
    }
    for (unsigned mode = CE_COMPOSE_ORIGIN_COHERENT; mode <= CE_COMPOSE_ORIGIN_VIEW; ++mode) {
        CeCatalog catalog = {.syntax=&syntax,.composition=mode}; CeModule *module; CePresentation result;
        require(ce_module_load(&catalog,"tests/modules/OriginProfiles.module",&module,&error),error.message);
        CeModule *aliased;
        require(ce_module_load(&catalog,"tests/modules/../modules/./OriginProfiles.module",&aliased,&error) && module == aliased,"canonical file identity shares declarations through path aliases");
        require(ce_module_check(&catalog,module,&error),error.message);
        require(ce_module_theory(&catalog,module,"Shared",&result,&error) && result.len == 1,"both profiles deduplicate one unchanged origin");
        require(ce_module_theory(&catalog,module,"RepeatedJoin",&result,&error) && result.len == 1,"repeated theory evaluation preserves declaration identity in joins");
        uint64_t base_origin = result.definitions[0].origin;
        require(ce_module_theory(&catalog,module,"RepeatedMeet",&result,&error) && result.len == 1 && result.definitions[0].origin == base_origin,"repeated evaluation preserves declaration identity in meets");
        require(ce_module_theory(&catalog,module,"RepeatedDifference",&result,&error) && result.len == 0,"a repeatedly evaluated theory subtracts its own declarations");
        require(!ce_module_theory(&catalog,module,"Independent",&result,&error),"both profiles refuse independent constructor label collision");
        bool coherent = mode == CE_COMPOSE_ORIGIN_COHERENT;
        require(ce_module_theory(&catalog,module,"RenamedJoin",&result,&error) != coherent,"renamed join separates composition profiles");
        if (!coherent) {
            require(result.len == 2,"origin-view join retains two views");
            require(result.definitions[0].origin == result.definitions[1].origin,"renamed views preserve shared origin");
            require(!ce_tree_equal(&syntax,result.definitions[0].tree,result.definitions[1].tree),"renamed views have distinct structural bodies");
        }
        require(ce_module_theory(&catalog,module,"RenamedMeet",&result,&error),error.message);
        require(result.len == (coherent ? 1U : 0U),"renamed meet separates origin from view identity");
        require(ce_module_theory(&catalog,module,"RenamedDifference",&result,&error),error.message);
        require(result.len == (coherent ? 0U : 1U),"renamed difference separates origin from view identity");
        require(!ce_module_theory(&catalog,module,"WrongResult",&result,&error),"replacement cannot silently change result category");
        require(ce_module_load(&catalog,"tests/modules/LawComposition.module",&module,&error),error.message);
        require(ce_module_theory(&catalog,module,"Base",&result,&error) && result.len == 1 && result.nlaws == 2,"Terms and equations/rewrites have separate source projections");
        uint64_t law_origin = result.laws[0].origin;
        require(ce_module_theory(&catalog,module,"Join",&result,&error) && result.nlaws == 2 && result.laws[0].origin == law_origin,"law joins preserve and deduplicate origins");
        require(ce_module_theory(&catalog,module,"Meet",&result,&error) && result.nlaws == 2,"law meets retain shared declarations");
        require(ce_module_theory(&catalog,module,"Difference",&result,&error) && result.nlaws == 0,"law difference removes identical origins");
        require(ce_module_theory(&catalog,module,"IndependentLaws",&result,&error) && result.nlaws == 1 && result.len == 0,"structurally equal independent laws retain distinct identity");
        ce_catalog_free(&catalog);
    }
    CeCatalog catalog = {.syntax=&syntax};
    fixture(&catalog,"tests/modules/DuplicateParameter.module",false);
    fixture(&catalog,"tests/modules/UnboundActivation.module",false);
    ce_catalog_free(&catalog); ce_grammar_free(&syntax); ce_arena_free(&arena);
    printf("%s modules: %u checks passed; both composition profiles verified\n",argc == 3 ? "reference" : "theory",checks);
    return 0;
}
