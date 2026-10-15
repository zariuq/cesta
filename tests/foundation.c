#include "../src/cesta.h"

#include <stdlib.h>
#include <string.h>

static unsigned checks;
static void require(bool condition, const char *description) {
    ++checks;
    if (!condition) { fprintf(stderr, "FAIL: %s\n", description); exit(1); }
}

static unsigned parses(const char *rules, const char *source, bool expected) {
    CeArena arena = {0};
    CeGrammar grammar = {0};
    CeTokens tokens = {0};
    CeForest forest = {.root=-1};
    CeError error = {{0},0};
    require(ce_grammar_read(&grammar, ce_cstr(&arena, rules), &error), error.message);
    require(ce_grammar_finish(&grammar, &error), error.message);
    require(ce_lex(&grammar, ce_cstr(&arena, source), &tokens, &error), error.message);
    bool accepted = ce_parse(&grammar, &tokens, grammar.productions[0].lhs, &forest, &error);
    require(accepted == expected, source);
    unsigned count = ce_forest_count(&forest, 100);
    ce_forest_free(&forest); ce_tokens_free(&tokens); ce_grammar_free(&grammar); ce_arena_free(&arena);
    return count;
}

static CeText contents(CeArena *arena, FILE *file) {
    require(fflush(file) == 0 && fseek(file,0,SEEK_END) == 0,"seek generated stream");
    long len = ftell(file);
    require(len >= 0 && fseek(file,0,SEEK_SET) == 0,"rewind generated stream");
    CeText out = {ce_alloc(arena,(size_t)len+1),(size_t)len};
    require(fread(out.data,1,out.len,file) == out.len,"read generated stream");
    return out;
}

static void roundtrip(const char *rules, const char *source, const char *wire) {
    CeArena arena = {0}; CeGrammar g = {0}; CeTokens tokens = {0};
    CeForest f = {.root=-1}; CeError error = {{0},0}; CeTree *original, *decoded, *reparsed;
    require(ce_grammar_read(&g,ce_cstr(&arena,rules),&error),error.message);
    require(ce_grammar_finish(&g,&error),error.message);
    int entry = g.productions[0].lhs;
    require(ce_lex(&g,ce_cstr(&arena,source),&tokens,&error),error.message);
    require(ce_parse(&g,&tokens,entry,&f,&error),error.message);
    require(ce_tree_from_forest(&g,&f,&tokens,&arena,&original,&error),error.message);
    FILE *file = tmpfile(); require(file != NULL,"open codec stream");
    ce_tree_encode(&g,original,file);
    CeText encoded = contents(&arena,file); fclose(file);
    if (wire) require(ce_is(encoded,wire),"AST S-expression image");
    require(ce_tree_decode(&g,encoded,entry,&arena,&decoded,&error),error.message);
    require(ce_tree_equal(&g,original,decoded),"decode(encode(tree)) equals tree");
    file = tmpfile(); require(file != NULL,"open printer stream");
    require(ce_tree_print(&g,decoded,entry,file,&error),error.message);
    CeText printed = contents(&arena,file); fclose(file);
    ce_forest_free(&f); ce_tokens_free(&tokens);
    require(ce_lex(&g,printed,&tokens,&error),error.message);
    require(ce_parse(&g,&tokens,entry,&f,&error),error.message);
    require(ce_tree_from_forest(&g,&f,&tokens,&arena,&reparsed,&error),error.message);
    require(ce_tree_equal(&g,original,reparsed),"parse(print(tree)) equals tree");
    file = tmpfile(); require(file != NULL,"open idempotence stream");
    require(ce_tree_print(&g,reparsed,entry,file,&error),error.message);
    require(ce_equal(printed,contents(&arena,file)),"printer idempotence"); fclose(file);
    ce_forest_free(&f); ce_tokens_free(&tokens); ce_grammar_free(&g); ce_arena_free(&arena);
}

static void reject_grammar(const char *rules) {
    CeArena a = {0}; CeGrammar g = {0}; CeError error = {{0},0};
    require(!ce_grammar_read(&g,ce_cstr(&a,rules),&error) || !ce_grammar_finish(&g,&error),"invalid grammar rejected");
    require(error.message[0] != 0,"grammar refusal identifies cause");
    ce_grammar_free(&g); ce_arena_free(&a);
}

static void reject_wire(const char *rules, const char *wire) {
    CeArena a = {0}; CeGrammar g = {0}; CeError error = {{0},0}; CeTree *tree;
    require(ce_grammar_read(&g,ce_cstr(&a,rules),&error),error.message);
    require(ce_grammar_finish(&g,&error),error.message);
    require(!ce_tree_decode(&g,ce_cstr(&a,wire),g.productions[0].lhs,&a,&tree,&error),"invalid source S-expression rejected");
    require(error.message[0] != 0 && error.offset < strlen(wire),"codec refusal identifies cause and position");
    ce_grammar_free(&g); ce_arena_free(&a);
}

static void lr_controls(void) {
    CeArena a = {0}; CeGrammar g = {0}; CeLR lr = {0}; CeError error = {{0},0};
    const char *rules = "Add . E ::= E \"+\" T; _ . E ::= T; Lit . T ::= \"n\"; _ . T ::= \"(\" E \")\";";
    require(ce_grammar_read(&g,ce_cstr(&a,rules),&error) && ce_grammar_finish(&g,&error),error.message);
    require(ce_lr_prepare(&g,g.productions[0].lhs,&lr,&error),error.message);
    require(lr.conflicts == 0,"unambiguous expression grammar is LALR");
    const char alphabet[] = "n+()";
    for (unsigned len = 0; len <= 5; ++len) {
        unsigned limit = 1U << (2*len);
        for (unsigned code = 0; code < limit; ++code) {
            char source[6]; unsigned bits = code;
            for (unsigned i = 0; i < len; ++i) { source[i] = alphabet[bits&3U]; bits >>= 2; }
            source[len] = 0;
            CeTokens tokens = {0}; CeForest reference = {.root=-1}, fast = {.root=-1};
            require(ce_lex(&g,ce_cstr(&a,source),&tokens,&error),error.message);
            bool expected = ce_parse(&g,&tokens,lr.entry,&reference,&error);
            bool actual = ce_lr_parse(&g,&lr,&tokens,&fast,&error);
            require(actual == expected,"LALR acceptance agrees with packed reference");
            if (expected) {
                CeTree *x, *y;
                require(ce_forest_count(&reference,2) == 1,"reference tree unique in LALR control");
                require(ce_tree_from_forest(&g,&reference,&tokens,&a,&x,&error),error.message);
                require(ce_tree_from_forest(&g,&fast,&tokens,&a,&y,&error),error.message);
                require(ce_tree_equal(&g,x,y),"LALR tree agrees with reference");
            }
            ce_forest_free(&reference); ce_forest_free(&fast); ce_tokens_free(&tokens);
        }
    }
    ce_lr_free(&lr); ce_grammar_free(&g);
    require(ce_grammar_read(&g,ce_cstr(&a,"Lit . E ::= \"n\"; Add . E ::= E \"+\" E;"),&error) && ce_grammar_finish(&g,&error),error.message);
    require(ce_lr_prepare(&g,g.productions[0].lhs,&lr,&error) && lr.conflicts > 0,"LR preserves unresolved conflict cells");
    CeTokens tokens = {0}; CeForest f = {.root=-1};
    require(ce_lex(&g,ce_cstr(&a,"n+n+n"),&tokens,&error),error.message);
    require(!ce_lr_parse(&g,&lr,&tokens,&f,&error),"undeclared LR conflicts forbid the fast path");
    ce_forest_free(&f); ce_tokens_free(&tokens); ce_lr_free(&lr); ce_grammar_free(&g); ce_arena_free(&a);
}

static void byte_controls(void) {
    CeArena a = {0}; unsigned char bytes[256];
    for (unsigned i = 0; i < 256; ++i) bytes[i] = (unsigned char)i;
    FILE *file = tmpfile(); require(file != NULL,"open byte codec stream");
    ce_quote(file,(CeText){bytes,sizeof(bytes)});
    CeText wire = contents(&a,file); fclose(file);
    CeError error = {{0},0}; CeMetaScanner scanner = {&a,wire,0,{{NULL,0},0,0},&error};
    require(ce_meta_next(&scanner) && scanner.token.kind == CE_META_STRING,error.message);
    require(ce_equal(scanner.token.text,(CeText){bytes,sizeof(bytes)}),"every byte survives quoted byte codec");
    require(ce_meta_next(&scanner) && scanner.token.kind == CE_META_END,"byte quote consumes exactly one string");
    ce_arena_free(&a);
}

static void dfa_controls(void) {
    CeArena a = {0}; CeGrammar g = {0}; CeDFA dfa = {0}; CeError error = {{0},0};
    const char *rules = "If . S ::= \"if\"; Root . S ::= Ident Number; token Number (digit+ ('.' digit+)?); comment \"//\"; comment \"/*\" \"*/\";";
    require(ce_grammar_read(&g,ce_cstr(&a,rules),&error) && ce_grammar_finish(&g,&error),error.message);
    require(ce_dfa_prepare(&g,&dfa,&error) && dfa.len > 1,error.message);
    const char alphabet[] = "ifx01./* ";
    for (unsigned len = 0; len <= 4; ++len) {
        unsigned limit = 1;
        for (unsigned i = 0; i < len; ++i) limit *= (unsigned)(sizeof(alphabet)-1);
        for (unsigned code = 0; code < limit; ++code) {
            char source[5]; unsigned digits = code;
            for (unsigned i = 0; i < len; ++i) { source[i] = alphabet[digits%(sizeof(alphabet)-1)]; digits /= (unsigned)(sizeof(alphabet)-1); }
            source[len] = 0; CeText input = {(unsigned char *)source,len};
            CeTokens reference = {0}, fast = {0}; CeError first = {{0},0}, second = {{0},0};
            bool expected = ce_lex(&g,input,&reference,&first);
            bool actual = ce_dfa_lex(&g,&dfa,input,&fast,&second);
            require(actual == expected,"DFA acceptance agrees with NFA reference");
            require(reference.len == fast.len,"DFA token cardinality agrees with NFA reference");
            if (!expected) require(first.offset == second.offset,"DFA refusal position agrees with NFA reference");
            for (size_t i = 0; i < reference.len; ++i)
                require(reference.data[i].symbol == fast.data[i].symbol && reference.data[i].offset == fast.data[i].offset && ce_equal(reference.data[i].text,fast.data[i].text),"DFA token identity, spelling and position agree");
            ce_tokens_free(&reference); ce_tokens_free(&fast);
        }
    }
    ce_dfa_free(&dfa); ce_grammar_free(&g); ce_arena_free(&a);
}

static void policy_controls(void) {
    CeArena a = {0}; CeGrammar g = {0}; CeLR lr = {0}; CeError error = {{0},0};
    const char *rules = "If . S ::= \"if\" S; IfElse . S ::= \"if\" S \"else\" S; Lit . S ::= \"a\";";
    require(ce_grammar_read(&g,ce_cstr(&a,rules),&error) && ce_grammar_finish(&g,&error),error.message);
    require(ce_profile_read(&g,ce_cstr(&a,"profile NearestElse; conflict \"else\" prefer shift over reduce \"If\" \"S\";"),&error),error.message);
    require(ce_lr_prepare(&g,g.productions[0].lhs,&lr,&error) && lr.ambiguities == 1 && lr.conflicts == 0,"named nearest-else policy admits exactly the declared conflict");
    CeTokens tokens = {0}; CeForest fast = {.root=-1}, reference = {.root=-1}; CeTree *tree = NULL;
    require(ce_lex(&g,ce_cstr(&a,"if if a else a"),&tokens,&error),error.message);
    require(ce_parse(&g,&tokens,lr.entry,&reference,&error) && ce_forest_count(&reference,2) == 2,"reference retains dangling-else ambiguity");
    require(ce_lr_parse(&g,&lr,&tokens,&fast,&error) && ce_tree_from_forest(&g,&fast,&tokens,&a,&tree,&error),error.message);
    require(ce_tree_label(&g,tree,"If") && ce_tree_label(&g,tree->children[0],"IfElse"),"declared policy chooses the nearest eligible if");
    ce_forest_free(&fast); ce_forest_free(&reference); ce_tokens_free(&tokens); ce_lr_free(&lr); ce_grammar_free(&g);
    rules = "Start . S ::= A \"x\" \"y\"; Other . S ::= \"x\" \"z\"; Empty . A ::=;";
    require(ce_grammar_read(&g,ce_cstr(&a,rules),&error) && ce_grammar_finish(&g,&error),error.message);
    require(ce_profile_read(&g,ce_cstr(&a,"profile Lookahead; conflict \"x\" prefer shift over reduce \"Empty\" \"A\" when next \"z\";"),&error),error.message);
    require(ce_lr_prepare(&g,g.productions[0].lhs,&lr,&error) && lr.ambiguities == 1 && lr.conflicts == 0,"conditional policy admits its exact LR conflict");
    const char *sources[] = {"xy","xz","xx","x"};
    for (size_t i = 0; i < sizeof(sources)/sizeof(*sources); ++i) {
        require(ce_lex(&g,ce_cstr(&a,sources[i]),&tokens,&error),error.message);
        bool expected = ce_parse(&g,&tokens,lr.entry,&reference,&error);
        require(ce_lr_parse(&g,&lr,&tokens,&fast,&error) == expected,"conditional LR lookahead preserves reference acceptance");
        if (expected) {
            CeTree *x = NULL, *y = NULL;
            require(ce_tree_from_forest(&g,&reference,&tokens,&a,&x,&error) && ce_tree_from_forest(&g,&fast,&tokens,&a,&y,&error),error.message);
            require(ce_tree_equal(&g,x,y),"conditional LR lookahead preserves unique reference tree");
        }
        ce_forest_free(&fast); ce_forest_free(&reference); ce_tokens_free(&tokens);
    }
    ce_lr_free(&lr); ce_grammar_free(&g); ce_arena_free(&a);
}

static void multiple_policy_controls(void) {
    const char *rules = "Start . S ::= A; ViaB . A ::= B; ViaC . A ::= C; ViaD . A ::= D; "
        "BLeaf . B ::= \"x\"; CLeaf . C ::= \"x\"; DLeaf . D ::= \"x\";";
    const char *profiles[] = {
        "profile None;",
        "profile Partial; conflict any prefer reduce \"BLeaf\" \"B\" over reduce \"CLeaf\" \"C\";",
        "profile Winner; conflict any prefer reduce \"BLeaf\" \"B\" over reduce \"CLeaf\" \"C\"; "
            "conflict any prefer reduce \"BLeaf\" \"B\" over reduce \"DLeaf\" \"D\";",
        "profile Cycle; conflict any prefer reduce \"BLeaf\" \"B\" over reduce \"CLeaf\" \"C\"; "
            "conflict any prefer reduce \"CLeaf\" \"C\" over reduce \"DLeaf\" \"D\"; "
            "conflict any prefer reduce \"DLeaf\" \"D\" over reduce \"BLeaf\" \"B\";",
        "profile Overlap; conflict any prefer reduce \"BLeaf\" \"B\" over reduce \"CLeaf\" \"C\"; "
            "conflict \"EOF\" prefer reduce \"BLeaf\" \"B\" over reduce \"CLeaf\" \"C\";"
    };
    for (size_t i = 0; i < sizeof(profiles)/sizeof(*profiles); ++i) {
        CeArena a = {0}; CeGrammar g = {0}; CeLR lr = {0}; CeError error = {{0},0};
        require(ce_grammar_read(&g,ce_cstr(&a,rules),&error) && ce_grammar_finish(&g,&error),error.message);
        require(ce_profile_read(&g,ce_cstr(&a,profiles[i]),&error),error.message);
        bool prepared = ce_lr_prepare(&g,g.productions[0].lhs,&lr,&error);
        if (i == 4) require(!prepared,"overlapping action-pair declarations are refused");
        else {
            require(prepared && lr.ambiguities == 1,error.message);
            require((lr.conflicts == 0) == (i == 2),"each alternative needs a direct winning preference");
            CeTokens tokens = {0}; CeForest f = {.root=-1}; CeTree *tree = NULL;
            require(ce_lex(&g,ce_cstr(&a,"x"),&tokens,&error),error.message);
            bool parsed = ce_lr_parse(&g,&lr,&tokens,&f,&error);
            require(parsed == (i == 2),"partial and cyclic policies cannot enable LR parsing");
            if (parsed) {
                require(ce_tree_from_forest(&g,&f,&tokens,&a,&tree,&error),error.message);
                require(ce_tree_label(&g,tree->children[0],"ViaB"),"declared three-way winner selects the intended tree");
            }
            ce_forest_free(&f); ce_tokens_free(&tokens);
        }
        ce_lr_free(&lr); ce_grammar_free(&g); ce_arena_free(&a);
    }
}

static void long_list_control(void) {
    CeArena a = {0}; CeGrammar g = {0}; CeLR lr = {0}; CeDFA dfa = {0}; CeTokens tokens = {0};
    CeForest f = {.root=-1}; CeError error = {{0},0}; CeTree *tree, *decoded;
    require(ce_grammar_read(&g,ce_cstr(&a,"Root . S ::= [Ident]; separator nonempty Ident \",\";"),&error) && ce_grammar_finish(&g,&error),error.message);
    require(ce_lr_prepare(&g,g.productions[0].lhs,&lr,&error) && lr.conflicts == 0,error.message);
    require(ce_dfa_prepare(&g,&dfa,&error),error.message);
    const size_t count = 50000; CeText input = {ce_alloc(&a,count*2),count*2-1};
    for (size_t i = 0; i < input.len; ++i) input.data[i] = i%2 ? ',' : 'x';
    require(ce_dfa_lex(&g,&dfa,input,&tokens,&error),error.message);
    require(ce_lr_parse(&g,&lr,&tokens,&f,&error),error.message);
    require(ce_tree_from_forest(&g,&f,&tokens,&a,&tree,&error),error.message);
    require(tree->len == 1 && tree->children[0]->len == count,"long list preserves all elements");
    FILE *file = tmpfile(); require(file != NULL,"open long list wire stream");
    ce_tree_encode(&g,tree,file); CeText wire = contents(&a,file); fclose(file);
    require(ce_tree_decode(&g,wire,lr.entry,&a,&decoded,&error),error.message);
    require(ce_tree_equal(&g,tree,decoded),"long list byte codec round trip");
    file = tmpfile(); require(file != NULL,"open long list printer stream");
    require(ce_tree_print(&g,decoded,lr.entry,file,&error),error.message);
    CeText printed = contents(&a,file); fclose(file);
    ce_forest_free(&f); ce_tokens_free(&tokens);
    require(ce_dfa_lex(&g,&dfa,printed,&tokens,&error) && ce_lr_parse(&g,&lr,&tokens,&f,&error),error.message);
    require(ce_tree_from_forest(&g,&f,&tokens,&a,&decoded,&error) && ce_tree_equal(&g,tree,decoded),"50,000-item list print/parse round trip");
    ce_forest_free(&f); ce_tokens_free(&tokens); ce_dfa_free(&dfa); ce_lr_free(&lr); ce_grammar_free(&g); ce_arena_free(&a);
}

int main(void) {
    require(parses("Lit . E ::= \"n\"; Add . E ::= E \"+\" E;", "n+n+n", true) == 2, "preserve expression ambiguity");
    require(parses("Lit . E ::= \"n\"; Add . E ::= E \"+\" E;", "n+", false) == 0, "missing operand rejected");
    require(parses("Root . S ::= A A; Empty . A ::=; Token . A ::= \"a\";", "a", true) == 2, "nullable completions preserve both positions");
    require(parses("Root . S ::= A A; Empty . A ::=; Token . A ::= \"a\";", "", true) == 1, "nullable empty source");
    require(parses("Root . S ::= [Ident]; separator Ident \",\";", "a,b,c", true) == 1, "separated list");
    require(parses("Root . S ::= [Ident]; separator Ident \",\";", "a,", true) == 1, "nullable separator tail retained");
    require(parses("Root . S ::= [Ident]; terminator Ident \";\";", "a;b;", true) == 1, "terminated list");
    require(parses("Root . S ::= T; token T ('x' ('y' | 'z')*);", "xyzy", true) == 1, "token regex alternation and repetition");
    require(parses("Root . S ::= T; token T (char - [\"x\"]);", "y", true) == 1, "character-set difference");
    require(parses("Root . S ::= A; A1 . A ::= \"a\"; A2 . A ::= A;", "a", true) == 100, "cyclic ambiguity saturates explicitly");
    CeGrammar g = {0}; CeArena a = {0}; CeError error = {{0},0};
    require(ce_grammar_read(&g, ce_cstr(&a, "A . E ::= \"a\"; A . E ::= \"b\";"), &error), "read duplicate constructors");
    require(!ce_grammar_finish(&g, &error), "reject duplicate constructors");
    ce_grammar_free(&g);
    require(ce_grammar_read(&g, ce_cstr(&a, "Root . E ::= T; token T 'x'*;"), &error), "read nullable token");
    require(!ce_grammar_finish(&g, &error), "reject nullable token");
    ce_grammar_free(&g); ce_arena_free(&a);
    roundtrip("Root . S ::= Ident;","x","(c:Root x)");
    roundtrip("Root . S ::= Ident;","True","(c:Root (c:Symbol \"True\"))");
    roundtrip("Root . S ::= Ident;","x'","(c:Root (c:Symbol \"x'\"))");
    roundtrip("_ . S ::= Ident;","x","x");
    roundtrip("_ . S ::= \"before\" Ident \"after\";","before x after","x");
    roundtrip("_ . S ::= [Ident]; separator Ident \",\";","x,y","(c:List x y)");
    roundtrip("Root . S ::= [Ident]; [] . [Ident] ::= \"empty\"; (:[]) . [Ident] ::= Ident; (:) . [Ident] ::= [Ident] \",\" Ident;","x,y,z","(c:Root (c:List x y z))");
    roundtrip("Root . S ::= [Ident]; [] . [Ident] ::= \"empty\"; (:[]) . [Ident] ::= Ident; (:) . [Ident] ::= [Ident] \",\" Ident;","empty","(c:Root (c:List))");
    roundtrip("Root . S ::= [Ident]; [] . [Ident] ::= \"empty\"; (:) . [Ident] ::= Ident \",\" [Ident];","x,y,empty","(c:Root (c:List x y))");
    roundtrip("Root . S ::= [Ident]; terminator Ident \";\";","x;y;","(c:Root (c:List x y))");
    roundtrip("Root . S ::= [Ident]; _ . E ::= Ident; _ . [Ident] ::= [E]; separator nonempty Ident \",\"; separator E \",\";","","(c:Root (c:List))");
    roundtrip("Root . S ::= [Ident]; _ . E ::= Ident; _ . [Ident] ::= \"begin\" [E] \"end\"; separator nonempty Ident \",\"; separator E \",\";","begin end","(c:Root (c:List))");
    roundtrip("Add . E ::= E \"+\" T; _ . E ::= T; Lit . T ::= \"n\"; _ . T ::= \"(\" E \")\";","n+(n+n)","(c:Add (c:Lit) (c:Add (c:Lit) (c:Lit)))");
    reject_wire("Root . S ::= Ident;","(c:Root (c:Symbol \"x\"))");
    reject_wire("Root . S ::= Ident;","(c:Root True)");
    reject_wire("Root . S ::= Ident;","(c:Root x')");
    reject_wire("Root . S ::= Ident; Literal . S ::= \"keyword\";","(c:Root keyword)");
    reject_wire("Public . S ::= \"a\"; internal Private . S ::= \"b\";","(c:Private)");
    reject_wire("Lit . E ::= \"a\"; _ . S ::= E;","(c:_ (c:Lit))");
    reject_wire("Root . S ::= [Ident]; separator nonempty Ident \",\";","(c:Root (c:List))");
    reject_wire("Root . S ::= [Ident]; _ . E ::= Ident; _ . [E] ::= [Ident]; separator E \",\"; separator nonempty Ident \",\";","(c:Root (c:List))");
    reject_wire("A . Expr1 ::= \"a\"; B . Expr2 ::= \"b\";","(c:B)");
    reject_grammar("List . S ::= \"x\";"); reject_grammar("Symbol . S ::= \"x\";");
    reject_grammar("Root . S ::= [Ident]; [] . [Ident] ::=; Bad . [Ident] ::= \"b\" \"a\";");
    reject_grammar("Root . S ::= [Ident]; (:) . [Ident] ::= Ident;");
    byte_controls(); lr_controls(); dfa_controls(); policy_controls(); multiple_policy_controls(); long_list_control();
    printf("foundation: %u checks passed\n", checks);
    return 0;
}
