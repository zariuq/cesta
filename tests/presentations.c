#include "../src/cesta.h"

#include <stdlib.h>
#include <string.h>

static unsigned checks;
static void require(bool condition, const char *message) {
    ++checks;
    if (!condition) { fprintf(stderr,"FAIL: %s\n",message); exit(1); }
}
static int named(const CeGrammar *g, const char *name) {
    for (size_t i = 0; i < g->nsymbols; ++i) if (ce_is(g->symbols[i].name,name)) return (int)i;
    require(false,name); return -1;
}
static CeAtom *atom(CeArena *arena, const char *source) {
    CeError error = {{0},0}; CeAtom *result;
    require(ce_atom_read(arena,ce_cstr(arena,source),&result,&error),error.message);
    return result;
}
static CeTree *ast_tree(const CeGrammar *g, CeArena *arena, int sort, const char *source) {
    CeError error = {{0},0}; CeTree *tree;
    require(ce_tree_decode(g,ce_cstr(arena,source),sort,arena,&tree,&error),error.message); return tree;
}
static void counters(const CeTree *tree, uint64_t *constructors, uint64_t *lists, uint64_t *tokens) {
    if (tree->production == -1) ++*tokens;
    else if (tree->production == -2) ++*lists;
    else ++*constructors;
    for (size_t i = 0; i < tree->len; ++i) counters(tree->children[i],constructors,lists,tokens);
}
static void exact_view(const CeViewProfile *profile, CeArena *arena, int sort, const CeTree *tree,
                        const char *expected, bool complete) {
    CeError error = {{0},0}; CeAtom *surface; CeTree *expanded; CeViewStats stats = {0};
    require(ce_view_tree(profile,tree,sort,arena,&surface,&error),error.message);
    if (expected) require(ce_atom_equal(surface,atom(arena,expected)),"view has the declared short spelling");
    require(ce_view_expand(profile,surface,sort,arena,&expanded,&stats,&error),error.message);
    require(ce_tree_equal(profile->grammar,tree,expanded),"expansion preserves the AST");
    uint64_t constructors = 0, lists = 0, tokens = 0;
    counters(tree,&constructors,&lists,&tokens);
    require(stats.constructors == constructors && stats.lists == lists && stats.tokens == tokens,
            "coverage denominator counts the original AST, including absorbed wrappers");
    if (complete) require(stats.shortened == constructors,"all original constructors were genuinely covered");
    uint64_t sum = 0, short_sum = 0;
    for (size_t i = 0; i < stats.capacity; ++i) { sum += stats.by_constructor[i]; short_sum += stats.short_by_constructor[i]; }
    require(sum == stats.constructors && short_sum == stats.shortened,"per-constructor coverage totals agree");
    ce_view_stats_free(&stats);
}
static void refused(const CeViewProfile *profile, CeArena *arena, int sort, const char *source) {
    CeError error = {{0},0}; CeTree *tree;
    require(!ce_view_expand(profile,atom(arena,source),sort,arena,&tree,NULL,&error),"malformed surface is refused");
    require(error.message[0] != 0,"surface refusal identifies its cause");
}
static void case_export(FILE *file, const CeViewProfile *profile, CeArena *arena,
                         int sort, const CeTree *tree, bool first) {
    CeError error = {{0},0}; CeAtom *surface; CeTree *expanded;
    require(ce_view_tree(profile,tree,sort,arena,&surface,&error) &&
            ce_view_expand(profile,surface,sort,arena,&expanded,NULL,&error),error.message);
    FILE *stream = tmpfile(); require(stream != NULL,"case stream");
    ce_atom_write(surface,stream); long n = ftell(stream); require(n >= 0,"measure surface case");
    CeText text = {ce_alloc(arena,(size_t)n+1),(size_t)n}; rewind(stream);
    require(fread(text.data,1,text.len,stream) == text.len,"read surface case"); fclose(stream);
    if (!first) fputc(',',file);
    fputs("{\"sort\":",file); ce_quote(file,profile->grammar->symbols[profile->grammar->symbols[sort].sort].name);
    fputs(",\"surface\":",file); ce_quote(file,text);
    stream = tmpfile(); require(stream != NULL,"AST case stream");
    ce_tree_encode(profile->grammar,expanded,stream); n = ftell(stream); require(n >= 0,"measure AST case");
    text = (CeText){ce_alloc(arena,(size_t)n+1),(size_t)n}; rewind(stream);
    require(fread(text.data,1,text.len,stream) == text.len,"read AST case"); fclose(stream);
    fputs(",\"ast\":",file); ce_quote(file,text); fputc('}',file);
}
int main(void) {
    CeArena arena = {0}; CeGrammar g = {0}, syntax = {0}; CeError error = {{0},0}; CeText text;
    require(ce_read_file(&arena,"build/CGNU.cf",&text,&error) && ce_grammar_read(&g,text,&error) && ce_grammar_finish(&g,&error),error.message);
    require(ce_profile_load(&g,"grammar/CGNUC11.profile",&error),error.message);
    require(ce_read_file(&arena,"grammar/TheoryModule.cf",&text,&error) && ce_grammar_read(&syntax,text,&error) && ce_grammar_finish(&syntax,&error),error.message);
    CeCatalog catalog = {.syntax=&syntax}; CeModule *module; CePresentation source;
    require(ce_module_load(&catalog,"tests/modules/PresentationControls.module",&module,&error),error.message);
    CeViewProfile operators = {0}, words = {0};
    require(ce_module_theory(&catalog,module,"Operators",&source,&error) && ce_view_compile(&operators,&g,&catalog,&source,"operators",1,&error),error.message);
    require(ce_module_theory(&catalog,module,"Words",&source,&error) && ce_view_compile(&words,&g,&catalog,&source,"words",1,&error),error.message);
    CeCatalog embedded = {.syntax=&syntax}; CeModule *same;
    CeText identity = ce_cstr(&arena,"embedded:controls.module");
    CeText embedded_source = ce_cstr(&arena,"Module Embedded { Theory E() { Empty } }");
    require(ce_module_read(&embedded,identity,embedded_source,&same,&error),error.message);
    CeModule *again;
    require(ce_module_read(&embedded,identity,embedded_source,&again,&error) && same == again,"embedded source reuses exactly one module identity");
    require(!ce_module_read(&embedded,identity,ce_cstr(&arena,"Module Other { Theory E() { Empty } }"),&again,&error),"one module identity cannot silently change source bytes");
    ce_catalog_free(&embedded);
    int expr = named(&g,"Expr"), statement = named(&g,"Statement"), unit = named(&g,"TranslationUnit");
    CeTree *product = ast_tree(&g,&arena,expr,"(c:Multiply (c:Indirection (c:Var p)) (c:Var n))");
    exact_view(&operators,&arena,expr,product,"(* (* p) n)",true);
    exact_view(&words,&arena,expr,product,"(multiply (indirect (variable p)) (variable n))",true);
    CeTree *store = ast_tree(&g,&arena,statement,"(c:ExpressionStatement (c:SomeFullExpression (c:AddAssign (c:Indirection (c:Var p)) (c:IntConst \"001u\"))))");
    exact_view(&operators,&arena,statement,store,"(+= (* p) (int \"001u\"))",true);
    exact_view(&words,&arena,statement,store,"(assign-statement (indirect (variable p)) (integer \"001u\"))",true);
    CeTree *ret = ast_tree(&g,&arena,statement,"(c:JumpStatement (c:Return (c:NoFullExpression)))");
    exact_view(&operators,&arena,statement,ret,"(return)",true);
    CeTree *unusual = ast_tree(&g,&arena,expr,"(c:Subtract (c:Var p) (c:Var n))");
    exact_view(&operators,&arena,expr,unusual,"(c:Subtract p n)",false);
    CeTree *alias = ast_tree(&g,&arena,expr,"(c:Var asm)");
    exact_view(&operators,&arena,expr,alias,"asm",true);
    CeAtom *bump = atom(&arena,"(unit (fn void bump (params (param unsigned (ptr p))) (+= (* p) (int \"1u\"))))");
    CeTree *program;
    require(ce_view_expand(&operators,bump,unit,&arena,&program,NULL,&error),error.message);
    exact_view(&operators,&arena,unit,program,NULL,true);
    CeAtom *sealed = ce_view_document(&operators,unit,bump,&arena); const CeAtom *payload;
    require(ce_view_document_payload(&operators,unit,sealed,&payload,&error) && payload == bump,"document checks key, schema, rules and entry sort");
    require(!ce_view_document_payload(&words,unit,sealed,&payload,&error),"document refuses a different presentation");
    require(!ce_view_document_payload(&operators,expr,sealed,&payload,&error),"document refuses a different entry sort");
    for (size_t i = 1; i <= 4; ++i) {
        CeAtom *bad = ce_atom_new(&arena,CE_ATOM_LIST,(CeText){NULL,0},sealed->len);
        memcpy(bad->items,sealed->items,sealed->len*sizeof(*bad->items));
        bad->items[i] = atom(&arena,i == 1 ? "operators/2" : i == 4 ? "Expr" : "\"ffffffffffffffff\"");
        require(!ce_view_document_payload(&operators,unit,bad,&payload,&error),"version/schema/rules/sort mismatch is refused");
    }
    FILE *rule_file = fopen("build/presentation-kernel-rules.json","wb");
    require(rule_file && ce_view_rules_export(&operators,rule_file,&error),error.message); fclose(rule_file);
    FILE *schema_file = fopen("build/presentation-kernel-schema.json","wb");
    require(schema_file && ce_metadata_export(&g,schema_file,&error),error.message); fclose(schema_file);
    FILE *cases = fopen("build/presentation-kernel-cases.json","wb");
    require(cases != NULL,"case export file"); fputc('[',cases);
    case_export(cases,&operators,&arena,expr,product,true);
    case_export(cases,&operators,&arena,statement,store,false);
    case_export(cases,&operators,&arena,statement,ret,false);
    case_export(cases,&operators,&arena,expr,unusual,false);
    case_export(cases,&operators,&arena,expr,alias,false);
    case_export(cases,&operators,&arena,unit,program,false);
    fputs("]\n",cases); fclose(cases);
    require(ce_tree_emit_checked(&g,program,unit,"build/presentation-bump.c",&error),error.message);
    refused(&operators,&arena,expr,"(* p n p)");
    refused(&operators,&arena,expr,"(c:Imaginary p)");
    refused(&operators,&arena,expr,"(c:Return (c:NoFullExpression))");
    refused(&operators,&arena,expr,"(int \"0garbage\")");
    refused(&operators,&arena,statement,"(return p n)");
    refused(&operators,&arena,unit,"(unit)");
    const char *bad[] = {"DuplicateDispatch","WrongResult","WrongArgument","NonlinearHole","UnknownTarget","UnknownSource","CyclicPass","AmbiguousPass","WrongArity","DuplicateInterpretation","OpenHole","DuplicateLexical","WordLexicalOverlap","PassLexicalOverlap","PrefixLexicalOverlap","DuplicatePrefix","PrefixWordOverlap","PrefixPassOverlap","DottedList","PrefixedLiteral","OverlappingTuples","OverlappingDefaults"};
    for (size_t i = 0; i < sizeof(bad)/sizeof(*bad); ++i) {
        CeViewProfile profile = {0}; error.message[0] = 0;
        require(ce_module_theory(&catalog,module,bad[i],&source,&error),error.message);
        require(!ce_view_compile(&profile,&g,&catalog,&source,bad[i],1,&error),bad[i]);
        require(error.message[0] != 0,"profile refusal identifies its cause"); ce_view_free(&profile);
    }
    CeModule *compact_module; CeViewProfile compact = {0};
    require(ce_module_load(&catalog,"presentations/CompactC.module",&compact_module,&error) &&
        ce_module_theory(&catalog,compact_module,"CompactC",&source,&error) &&
        ce_view_compile(&compact,&g,&catalog,&source,"compact-c",1,&error),error.message);
    const char *lexical_cases[][2] = {
        {"\"001u\"","(c:IntConst \"001u\")"},
        {"\"1.0f\"","(c:FloatConst \"1.0f\")"},
        {"\"'a'\"","(c:CharConst \"'a'\")"},
        {"\"\\\"text\\\"\"","(c:StringLiterals (c:List \"\\\"text\\\"\"))"},
        {"p","(c:Var p)"},
        {"(c:Symbol \"PI\")","(c:Var (c:Symbol \"PI\"))"},
        {"(c:Symbol \"EXP\")","(c:Var (c:Symbol \"EXP\"))"},
        {"(c:Symbol \"True\")","(c:Var (c:Symbol \"True\"))"},
        {"(c:Symbol \"α\")","(c:Var (c:Symbol \"α\"))"}
    };
    for (size_t i = 0; i < sizeof(lexical_cases)/sizeof(*lexical_cases); ++i) {
        CeTree *expected, *expanded; CeViewStats stats = {0}; CeAtom *viewed;
        expected = ast_tree(&g,&arena,expr,lexical_cases[i][1]);
        require(ce_view_expand(&compact,atom(&arena,lexical_cases[i][0]),expr,&arena,&expanded,&stats,&error) &&
            ce_tree_equal(&g,expected,expanded),error.message);
        require(stats.shortened == stats.constructors,"lexical forms cover original constructors");
        require(ce_view_tree(&compact,expected,expr,&arena,&viewed,&error) &&
            ce_atom_equal(viewed,atom(&arena,lexical_cases[i][0])),"lexical display preserves its exact spelling");
        ce_view_stats_free(&stats);
    }
    refused(&compact,&arena,expr,"\"1garbage\"");
    refused(&compact,&arena,expr,"PI");
    refused(&compact,&arena,expr,"(c:Symbol p)");
    refused(&compact,&arena,expr,"(c:Symbol \"p\")");
    CeModule *cmera_module; CeViewProfile cmera = {0};
    require(ce_module_load(&catalog,"presentations/CMeraStyle.module",&cmera_module,&error) &&
        ce_module_theory(&catalog,cmera_module,"CMeraStyle",&source,&error) &&
        ce_view_compile(&cmera,&g,&catalog,&source,"cmera-style",1,&error),error.message);
    exact_view(&cmera,&arena,expr,product,"(* *p n)",true);
    exact_view(&cmera,&arena,statement,store,"(+= *p \"001u\")",true);
    CeTree *cmera_program;
    require(ce_view_expand(&cmera,atom(&arena,"(unit (function bump ((unsigned *p)) -> void (+= *p \"1u\")))"),unit,
        &arena,&cmera_program,NULL,&error) && ce_tree_equal(&g,cmera_program,program),error.message);
    exact_view(&cmera,&arena,unit,program,"(unit (function bump ((unsigned *p)) -> void (+= *p \"1u\")))",true);
    refused(&cmera,&arena,expr,"*1u");
    refused(&cmera,&arena,expr,"*");
    refused(&cmera,&arena,unit,"(unit (function bump ((unsigned *p)) => void (+= *p \"1u\")))");
    FILE *cmera_rules = fopen("build/presentation-cmera-controls-rules.json","wb");
    require(cmera_rules && ce_view_rules_export(&cmera,cmera_rules,&error),error.message); fclose(cmera_rules);
    FILE *cmera_schema = fopen("build/presentation-cmera-controls-schema.json","wb");
    require(cmera_schema && ce_metadata_export(&g,cmera_schema,&error),error.message); fclose(cmera_schema);
    FILE *cmera_cases = fopen("build/presentation-cmera-controls-cases.json","wb");
    require(cmera_cases != NULL,"C-Mera control cases"); fputc('[',cmera_cases);
    case_export(cmera_cases,&cmera,&arena,expr,product,true);
    case_export(cmera_cases,&cmera,&arena,statement,store,false);
    case_export(cmera_cases,&cmera,&arena,unit,program,false);
    fputs("]\n",cmera_cases); fclose(cmera_cases);
    ce_view_free(&cmera);
    ce_view_free(&compact);
    CeModule *compact2_module; CeViewProfile compact2 = {0};
    require(ce_module_load(&catalog,"presentations/CompactC2.module",&compact2_module,&error) &&
        ce_module_theory(&catalog,compact2_module,"CompactC2",&source,&error) &&
        ce_view_compile(&compact2,&g,&catalog,&source,"compact-c",2,&error),error.message);
    int specifiers = named(&g,"DeclarationSpecifiers"), structure = named(&g,"StructSpecifier"), item = named(&g,"BlockItem");
    int declarator = named(&g,"DirectDeclarator"), parameter = named(&g,"Parameter");
    /* A dotted tail continues a specifier sequence in place; one atom stays direct. */
    CeTree *storage = ast_tree(&g,&arena,specifiers,"(c:PrefixedDeclarationSpecifiers (c:StorageSpecifier (c:Static)) "
        "(c:PrefixedDeclarationSpecifiers (c:FunctionSpecifier (c:Inline)) (c:PrimitiveDeclarationSpecifiers (c:Int) (c:List))))");
    exact_view(&compact2,&arena,specifiers,storage,"(static inline int)",true);
    CeTree *nested;
    require(ce_view_expand(&compact2,atom(&arena,"(static (inline int))"),specifiers,&arena,&nested,NULL,&error) &&
        ce_tree_equal(&g,storage,nested),"a dotted tail may also be written as one nested atom");
    exact_view(&compact2,&arena,specifiers,ast_tree(&g,&arena,specifiers,
        "(c:PrefixedDeclarationSpecifiers (c:StorageSpecifier (c:Static)) (c:UniqueDeclarationSpecifiers (c:TypedefType size_t) (c:List)))"),
        "(static size_t)",true);
    refused(&compact2,&arena,specifiers,"(static)");
    /* Literal tokens are the atoms their spellings read as. */
    exact_view(&compact2,&arena,expr,ast_tree(&g,&arena,expr,"(c:IntConst \"0x2au\")"),"0x2au",true);
    exact_view(&compact2,&arena,expr,ast_tree(&g,&arena,expr,"(c:CharConst \"'a'\")"),"'a'",true);
    exact_view(&compact2,&arena,expr,ast_tree(&g,&arena,expr,"(c:StringLiterals (c:List \"\\\"hi\\\\n\\\"\"))"),"\"hi\\n\"",true);
    exact_view(&compact2,&arena,expr,ast_tree(&g,&arena,expr,"(c:StringLiterals (c:List \"\\\"\\\\x41\\\"\"))"),
        "(strings \"\\\"\\\\x41\\\"\")",true);
    exact_view(&compact2,&arena,expr,ast_tree(&g,&arena,expr,"(c:CharConst \"' '\")"),"(char \"' '\")",true);
    refused(&compact2,&arena,expr,"4abc");
    refused(&compact2,&arena,expr,"(char 'a')");
    /* One head, three readings told apart by the first atom of each position. */
    exact_view(&compact2,&arena,structure,ast_tree(&g,&arena,structure,"(c:StructReference (c:Struct) node)"),"(struct node)",true);
    exact_view(&compact2,&arena,structure,ast_tree(&g,&arena,structure,
        "(c:StructDefinition (c:Struct) (c:SomeIdent node) (c:List (c:StructDeclaration (c:PrimitiveSpecifierQualifiers (c:Int) (c:List)) "
        "(c:StructDeclarators (c:List (c:StructDeclarator (c:Declarator (c:NameDeclarator a))))))))"),"(struct node (int a))",true);
    exact_view(&compact2,&arena,structure,ast_tree(&g,&arena,structure,
        "(c:StructDefinition (c:Struct) (c:NoIdent) (c:List (c:StructDeclaration (c:PrimitiveSpecifierQualifiers (c:Int) (c:List)) "
        "(c:StructDeclarators (c:List (c:StructDeclarator (c:Declarator (c:NameDeclarator a))))))))"),"(struct (int a))",true);
    refused(&compact2,&arena,structure,"(struct)");
    /* Two unheaded defaults with disjoint first atoms. */
    exact_view(&compact2,&arena,item,ast_tree(&g,&arena,item,"(c:BlockDeclaration (c:Declaration (c:PrimitiveDeclarationSpecifiers (c:Int) (c:List)) "
        "(c:InitDeclarators (c:List (c:InitDeclarator (c:Declarator (c:NameDeclarator x)))))))"),"(var int x)",true);
    exact_view(&compact2,&arena,item,ast_tree(&g,&arena,item,"(c:BlockStatement (c:ExpressionStatement (c:SomeFullExpression "
        "(c:Call (c:Var f) (c:NoArguments)))))"),"(call f)",true);
    /* Fixed words may close a spliced list or follow a tuple's first field. */
    CeTree *variadic = ast_tree(&g,&arena,declarator,"(c:FunctionDeclarator (c:NameDeclarator f) (c:VariadicParameterTypes "
        "(c:List (c:NamedParameter (c:PrimitiveDeclarationSpecifiers (c:Int) (c:List)) (c:Declarator (c:NameDeclarator a))))))");
    exact_view(&compact2,&arena,declarator,variadic,"(function f (int a) ...)",true);
    refused(&compact2,&arena,declarator,"(function f ...)");
    exact_view(&compact2,&arena,parameter,ast_tree(&g,&arena,parameter,"(c:AbstractParameter (c:PrimitiveDeclarationSpecifiers (c:Char) (c:List)) "
        "(c:SomeAbstractDeclarator (c:AbstractPointer (c:Pointer (c:NoTypeQualifiers)))))"),"(char *)",true);
    FILE *compact2_rules = fopen("build/presentation-compact2-controls-rules.json","wb");
    require(compact2_rules && ce_view_rules_export(&compact2,compact2_rules,&error),error.message); fclose(compact2_rules);
    FILE *compact2_schema = fopen("build/presentation-compact2-controls-schema.json","wb");
    require(compact2_schema && ce_metadata_export(&g,compact2_schema,&error),error.message); fclose(compact2_schema);
    FILE *compact2_cases = fopen("build/presentation-compact2-controls-cases.json","wb");
    require(compact2_cases != NULL,"compact-c/2 control cases"); fputc('[',compact2_cases);
    case_export(compact2_cases,&compact2,&arena,specifiers,storage,true);
    case_export(compact2_cases,&compact2,&arena,declarator,variadic,false);
    case_export(compact2_cases,&compact2,&arena,unit,program,false);
    fputs("]\n",compact2_cases); fclose(compact2_cases);
    ce_view_free(&compact2);
    /* Atom spelling is not subject to a host reader's numeric coercions. */
    CeAtom *numeric = atom(&arena,"(001 1u PI True \"bytes\\x00\\xff\" λ)");
    require(numeric->items[0]->kind == CE_ATOM_SYMBOL && ce_is(numeric->items[0]->text,"001"),"leading zero spelling remains a symbol");
    require(numeric->items[2]->kind == CE_ATOM_SYMBOL && numeric->items[3]->kind == CE_ATOM_SYMBOL,"reserved host names remain symbols in the presentation reader");
    const char *bad_atoms[] = {"(",")","x y","$hole","\"\\q\"","\"\\x0\"","\"unfinished"};
    for (size_t i = 0; i < sizeof(bad_atoms)/sizeof(*bad_atoms); ++i) {
        CeAtom *unused; error.message[0] = 0;
        require(!ce_atom_read(&arena,ce_cstr(&arena,bad_atoms[i]),&unused,&error) && error.message[0],"malformed atom input is refused");
    }
    unsigned char invalid_utf8[] = {0xed,0xa0,0x80}; CeAtom *unused;
    require(!ce_atom_read(&arena,(CeText){invalid_utf8,sizeof(invalid_utf8)},&unused,&error),"UTF-8 surrogate symbols are refused");
    FILE *bytes = tmpfile(); require(bytes != NULL,"byte round-trip stream");
    unsigned char values[256]; for (size_t i = 0; i < sizeof(values); ++i) values[i] = (unsigned char)i;
    CeAtom *all_bytes = ce_atom_new(&arena,CE_ATOM_TEXT,ce_text(&arena,values,sizeof(values)),0);
    ce_atom_write(all_bytes,bytes); long size = ftell(bytes); require(size > 0,"measure byte stream");
    rewind(bytes); text = (CeText){ce_alloc(&arena,(size_t)size+1),(size_t)size};
    require(fread(text.data,1,text.len,bytes) == text.len,"read byte stream"); fclose(bytes);
    require(ce_atom_read(&arena,text,&unused,&error) && ce_atom_equal(all_bytes,unused),"all 256 bytes survive the atom codec");
    ce_view_free(&operators); ce_view_free(&words); ce_catalog_free(&catalog);
    ce_grammar_free(&syntax); ce_grammar_free(&g); ce_arena_free(&arena);
    printf("presentation kernel: %u checks; typed data rules, exact fallback, original-node coverage and refusals passed\n",checks);
    return 0;
}
