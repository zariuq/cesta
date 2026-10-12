#include "../src/cesta.h"

#include <stdlib.h>
#include <string.h>

static unsigned checks;
static const char *current;
static void require(bool ok, const char *message) {
    ++checks;
    if (!ok) { fprintf(stderr,"FAIL: %s: %s\n",current ? current : "setup",message); exit(1); }
}
static CeText stream(CeArena *a, FILE *file) {
    require(fflush(file) == 0 && fseek(file,0,SEEK_END) == 0,"seek output");
    long size = ftell(file); require(size >= 0 && fseek(file,0,SEEK_SET) == 0,"rewind output");
    CeText text = {ce_alloc(a,(size_t)size+1),(size_t)size};
    require(fread(text.data,1,text.len,file) == text.len,"read output"); return text;
}
static unsigned count(const CeGrammar *g, const CeTree *tree, const char *label) {
    unsigned n = ce_tree_label(g,tree,label);
    for (size_t i = 0; i < tree->len; ++i) n += count(g,tree->children[i],label);
    return n;
}
typedef struct { const char *name, *source, *label; unsigned expected; bool accept; } Case;
static const Case cases[] = {
    {"ordinary multiplication", "void f(void){int T,x;T*x;}", "Multiply",1,true},
    {"unspaced hexadecimal pp exponent refused", "int f(void){return 0xE+2;}",NULL,0,false},
    {"unspaced hexadecimal pp suffix refused", "int foo;int f(void){return 0xe-foo;}",NULL,0,false},
    {"invalid pp number cannot become member syntax", "int f(void){return .123.foo;}",NULL,0,false},
    {"reserved imaginary keyword cannot be an identifier", "int _Imaginary;",NULL,0,false},
    {"adjacent static-assert message literals", "_Static_assert(1,\"a\" \"b\");_Static_assert(1,\"a\" L\"b\");", "StaticAssertion",2,true},
    {"Unicode typedef name", "typedef int α;α*x;", "TypedefType",1,true},
    {"digraph punctuators", "int a<:2:>=<%1,2%>;int f(void)<%return a<:0:>;%>", "Subscript",1,true},
    {"UCN typedef equivalent to UTF-8 spelling", "typedef int \\u03b1;α*x;", "TypedefType",1,true},
    {"UTF-8 typedef equivalent to long UCN spelling", "typedef int α;\\U000003b1*x;", "TypedefType",1,true},
    {"UCN shadow restored by lexical scope", "typedef int α;void f(void){{int \\u03b1,x;α*x;}α*y;}", "Multiply",1,true},
    {"typedef pointer declaration", "typedef int T;void f(void){T*x;}", "TypedefType",1,true},
    {"block shadow and restoration", "typedef int T;void f(void){T*x;{int T,x;T*x;}T*y;}", "Multiply",1,true},
    {"complete declarator binding", "typedef int T,T1(T);void f(void){int(*T)(T x)=0;int T1=sizeof((int)T1);}", "TypedefType",2,true},
    {"typedef parameter ambiguity", "typedef int T;void f(int(x),int(T),int T);", "NamedParameter",2,true},
    {"prototype restoration", "typedef int T;void f(int T);T x;", "TypedefType",1,true},
    {"parameter scope in body", "typedef int T;void f(int T){int x;T*x;}", "Multiply",1,true},
    {"function returning function pointer", "typedef int T;int(*f(int T))(int x){T*x;return 0;}", "Multiply",1,true},
    {"member and tag namespaces", "typedef int T;struct T{int T;};void f(void){struct T x;x.T=1;T y;goto T;T:y=0;}", "TypedefType",1,true},
    {"enum shadows typedef", "typedef int T;void f(void){enum{T=1};int x;T*x;}", "Multiply",1,true},
    {"enumerator visible after initializer", "typedef char T;void f(void){enum{T=sizeof(T)};sizeof(T);}", "SizeofType",1,true},
    {"primitive versus unique type", "typedef int T;void f(void){const T;unsigned T;T=1;}", "TypedefType",1,true},
    {"bitfield names", "typedef int T;struct S{unsigned T:4;const T:5;};", "TypedefType",1,true},
    {"for declaration scope", "typedef int T;void f(void){for(int T=0;T<2;T++){T*T;}T*x;}", "Multiply",1,true},
    {"selection scope restoration", "typedef int T;void f(void){if(sizeof(enum{T=1}))T*T;T*x;}", "Multiply",1,true},
    {"do body restoration before condition", "typedef int T;void f(void){do{enum{T=1};}while(sizeof(T));}", "SizeofType",1,true},
    {"old style parameter scope", "typedef int T;void f(x)int x;{x*x;}T y;", "TypedefType",1,true},
    {"nearest else", "void f(int x){if(x)if(x)x++;else x--;}", "IfElse",1,true},
    {"typedef in expression refused", "typedef int T;void f(void){T+1;}",NULL,0,false},
    {"variable in type position refused", "void f(void){int T;(T*)0;}",NULL,0,false},
    {"prototype-local typedef cannot leak", "void f(enum{T});T x;",NULL,0,false},
    {"missing declaration type refused", "void f(void){const x;}",NULL,0,false},
    {"mixed unique type refused", "typedef int T;T int x;",NULL,0,false},
    {"atomic type", "_Atomic(int) x;_Atomic int y;", "AtomicType",1,true},
    {"atomic parenthesis cannot become qualifier", "int _Atomic(x);",NULL,0,false}
};

static const Case gnu_cases[] = {
    {"GNU omitted middle conditional", "int f(int x){return x?:7;}", "GNUConditional",1,true},
    {"GNU range designator", "int x[5]={[0 ... 3]=7,[4]=9};", "GNUArrayRangeDesignator",1,true},
    {"GNU nested range designator", "int x[2][3]={[0 ... 1][1 ... 2]=4};", "GNUArrayRangeDesignator",2,true},
    {"GNU range without endpoint refused", "int x[5]={[0 ...]=7};",NULL,0,false},
    {"GNU omitted middle missing alternate refused", "int f(int x){return x?:;}",NULL,0,false},
    {"GNU empty aggregate initializer", "struct S{int x;};struct S x={};", "GNUEmptyInitializer",1,true},
    {"GNU empty compound literal", "struct S{int x;};struct S f(void){return (struct S){};}", "GNUEmptyCompoundLiteral",1,true},
    {"GNU inferred declaration delays shadowing", "typedef int T;void f(void){__auto_type T=sizeof(T);sizeof(T);}", "SizeofType",1,true},
    {"GNU inferred initializer keeps outer typedef", "typedef int T;void f(void){__auto_type T=({T value=1;value;});T*T;}", "Multiply",1,true},
    {"GNU nested inferred declarations preserve contexts", "typedef int T;void f(void){__auto_type x=({__auto_type T=sizeof(T);sizeof(T);});T y;}", "SizeofType",1,true},
    {"GNU thread storage", "static __thread unsigned long x;", "GNUThread",1,true},
    {"GNU predefined wide types and shadow", "__uint128_t x;void f(void){int __int128_t;__int128_t*__int128_t;}", "Multiply",1,true},
    {"GNU const attribute spellings", "extern int f(void)__attribute__((__const__,__const,const));", "GNUAttribute",1,true},
    {"GNU member extension", "union S{__extension__ unsigned long long x;};", "GNUStructDeclarationExtension",1,true},
    {"retained pragma lines", "#pragma GCC diagnostic push\nint f(void){\n#pragma GCC diagnostic ignored \"-Wunused-variable\"\nint x;return 1;}\n#pragma GCC diagnostic pop\n", "GNUPragma",3,true},
    {"compiler predefined type", "typedef __builtin_va_list va;va x;", "TypedefType",2,true},
    {"builtin typedef shadowing", "void f(void){int __builtin_va_list;__builtin_va_list*__builtin_va_list;}", "Multiply",1,true},
    {"GNU attributed function", "extern int f(void)__attribute__((nothrow,nonnull(1)));", "GNUAttribute",1,true},
    {"GNU statement expression scope", "typedef int T;int f(void){return ({int T=2;T*T;});}T x;", "Multiply",1,true},
    {"type-taking builtin", "struct S{int x;};int f(void){return __builtin_offsetof(struct S,x);}", "GNUOffsetof",1,true},
    {"GNU type query", "int x;__typeof__(x) y;__typeof__(int) z;", "GNUTypeofType",1,true},
    {"nested typedef does not classify outer typeof declaration", "void f(void){__typeof__(({typedef int X;1;})) y;y*y;}", "Multiply",1,true},
    {"outer typedef survives nested typeof variable declaration", "typedef __typeof__(({int X;1;})) T;T x;", "TypedefType",1,true},
    {"attribute on anonymous typedef parameter function", "typedef int T;void f(int(T)__attribute__((unused)));T x;", "GNUAttributedAbstractDeclarator",1,true},
    {"GNU extended asm", "int f(int x){__asm__ volatile(\"\":\"+r\"(x): :\"memory\");return x;}", "GNUAsmWithClobbers",1,true},
    {"GNU asm name", "extern int f(int)__asm__(\"real_f\");", "GNUAsmDeclarator",1,true},
    {"aligned member extension", "struct S{_Alignas(int) char x;};", "GNUAlignedMemberSpecifiers",1,true},
    {"missing builtin type refused", "int f(void){return __builtin_va_arg(0,2);}",NULL,0,false}
};

typedef struct { const char *source; bool iso, gnu; } LexCase;
static const Case dialect_cases[] = {
    {"omitted middle conditional is a GNU extension", "int f(int x){return x?:7;}", "GNUConditional",1,true},
    {"range designator is a GNU extension", "int x[5]={[0 ... 3]=7};", "GNUArrayRangeDesignator",1,true},
    {"extra external semicolon is a GNU extension", "int x;;", "GNUEmptyExternalDeclaration",1,true},
    {"extra member semicolon is a GNU extension", "struct S{int x;;};", "GNUEmptyStructDeclaration",1,true},
    {"obsolete GNU array designation omits equal sign", "int x[2]={[1]3};", "GNUOldArrayInitializer",1,true}
};
static const LexCase lex_cases[] = {
    {"'\\377' \"\\x00\\xff\" L'\\u03b1' u8\"λ\"",true,true},
    {"'\\e' \"\\q\"",false,true},
    {"'\\x'",false,false},
    {"\"\\x\"",false,false},
    {"\"\\u123\"",false,false},
    {"'\\U0012345'",false,false},
    {"\\u12",false,false},
    {"\\u0061",false,false},
    {"\\u0080",false,false},
    {"\\ud800",false,false},
    {"\\u0301",false,false},
    {"a\\u0301",true,true},
    {"\"\\u0061\"",false,false},
    {"'\\ud800'",false,false},
    {"\"\\U00110000\"",false,false},
    {"\"\\u0024\\u0040\\u0060\\u00a0\"",true,true},
    {"\xc0\xaf",false,false},
    {"$variable",false,true}
};

int main(int argc, char **argv) {
    require(argc == 3,"expected C grammar and parser profile");
    CeArena setup = {0}; CeGrammar g = {0}; CeLR lr = {0}; CeDFA dfa = {0}; CeError error = {{0},0};
    CeText source;
    require(ce_read_file(&setup,argv[1],&source,&error) && ce_grammar_read(&g,source,&error) && ce_grammar_finish(&g,&error),error.message);
    require(ce_profile_load(&g,argv[2],&error),error.message);
    int entry = g.entries[0];
    require(ce_lr_prepare(&g,entry,&lr,&error) && lr.conflicts == 0,"every conflict has an explicit policy");
    require(ce_dfa_prepare(&g,&dfa,&error),error.message);
    for (size_t i = 0; i < sizeof(lex_cases)/sizeof(*lex_cases); ++i) {
        current = lex_cases[i].source; CeTokens fast = {0}, reference = {0};
        CeText input = {(unsigned char *)lex_cases[i].source,strlen(lex_cases[i].source)};
        bool expected = ce_is(g.name,"CGNU") ? lex_cases[i].gnu : lex_cases[i].iso;
        bool first = ce_dfa_lex(&g,&dfa,input,&fast,&error);
        bool second = ce_lex(&g,input,&reference,&error);
        require(first == expected && second == expected,"literal and identifier admission agrees with the declared dialect");
        if (first) {
            require(fast.len == reference.len,"DFA and NFA lexical counts agree");
            for (size_t j = 0; j < fast.len; ++j)
                require(fast.data[j].symbol == reference.data[j].symbol && ce_equal(fast.data[j].text,reference.data[j].text),"DFA and NFA lexical values agree");
        }
        ce_tokens_free(&fast); ce_tokens_free(&reference);
    }
    size_t base_count = sizeof(cases)/sizeof(*cases);
    size_t shared_count = base_count + (ce_is(g.name,"CGNU") ? sizeof(gnu_cases)/sizeof(*gnu_cases) : 0);
    size_t total = shared_count + sizeof(dialect_cases)/sizeof(*dialect_cases);
    for (size_t i = 0; i < total; ++i) {
        const Case *test = i < base_count ? &cases[i] : i < shared_count ? &gnu_cases[i-base_count] : &dialect_cases[i-shared_count]; current = test->name;
        bool expected = i < shared_count ? test->accept : ce_is(g.name,"CGNU");
        CeArena a = {0}; CeTokens tokens = {0}; CeForest f = {.root=-1}; CeTree *tree, *decoded, *reparsed;
        require(ce_dfa_lex(&g,&dfa,ce_cstr(&a,test->source),&tokens,&error),error.message);
        bool accepted = ce_lr_parse(&g,&lr,&tokens,&f,&error);
        require(accepted == expected,error.message);
        if (accepted) {
            require(ce_tree_from_forest(&g,&f,&tokens,&a,&tree,&error),error.message);
            require(count(&g,tree,test->label) == test->expected,"scope chooses the expected syntactic reading");
            FILE *file = tmpfile(); require(file != NULL,"open codec stream");
            ce_tree_encode(&g,tree,file); CeText wire = stream(&a,file); fclose(file);
            require(ce_tree_decode(&g,wire,entry,&a,&decoded,&error) && ce_tree_equal(&g,tree,decoded),"sorted codec preserves the tree");
            file = tmpfile(); require(file != NULL,"open printer stream");
            require(ce_tree_print(&g,decoded,entry,file,&error),error.message);
            CeText printed = stream(&a,file); fclose(file);
            file = tmpfile(); require(file != NULL,"open prepared printer stream");
            require(ce_tree_print_prepared(&g,&dfa,decoded,entry,file,&error),error.message);
            require(ce_equal(printed,stream(&a,file)),"prepared and owned lexer tables produce identical source");
            fclose(file);
            ce_forest_free(&f); ce_tokens_free(&tokens);
            require(ce_dfa_lex(&g,&dfa,printed,&tokens,&error) && ce_lr_parse(&g,&lr,&tokens,&f,&error),error.message);
            require(ce_tree_from_forest(&g,&f,&tokens,&a,&reparsed,&error) && ce_tree_equal(&g,tree,reparsed),"scope-sensitive print/parse preserves the tree");
        }
        ce_forest_free(&f); ce_tokens_free(&tokens); ce_arena_free(&a);
    }
    printf("C scope: %u checks passed; %zu cases; %zu declared conflict cells\n",checks,total,lr.ambiguities);
    ce_dfa_free(&dfa); ce_lr_free(&lr); ce_grammar_free(&g); ce_arena_free(&setup); return 0;
}
