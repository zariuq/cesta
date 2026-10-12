#define _POSIX_C_SOURCE 200809L
#include "cesta.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

CeText ce_builtin_C11Grammar(void);
CeText ce_builtin_CGNUGrammar(void);
CeText ce_builtin_C11Profile(void);
CeText ce_builtin_CGNUProfile(void);
CeText ce_builtin_CGNUC11Profile(void);
CeText ce_builtin_TheoryModuleSyntax(void);

static bool suffix(const char *text, const char *end) {
    size_t a = strlen(text), b = strlen(end);
    return a >= b && !strcmp(text+a-b,end);
}
static bool option_value(const char *option) {
    const char *names[] = {"-o","-I","-L","-l","-D","-U","-MF","-MT","-MQ","-x","-include",
        "-imacros","-isystem","-iquote","-idirafter","-iprefix","-iwithprefix","-iwithprefixbefore",
        "-isysroot","--sysroot","-target","--target","-Xclang","-Xpreprocessor","-Xassembler","-Xlinker","-B"};
    for (size_t i = 0; i < sizeof(names)/sizeof(*names); ++i) if (!strcmp(option,names[i])) return true;
    return false;
}
static bool directories(char *path, CeError *error) {
    for (char *p = path+1;; ++p) if (*p == '/' || !*p) {
        char saved = *p; *p = 0;
        int result = mkdir(path,0777); *p = saved;
        if (result && errno != EEXIST) return ce_error(error,0,"cannot create generated-source directory: %s",strerror(errno));
        if (!saved) break;
    }
    return true;
}
bool ce_builtin_language(CeGrammar *g, bool gnu, CeError *error) {
    if (!ce_grammar_read(g,ce_builtin_CGNUGrammar(),error) || !ce_grammar_finish(g,error)) return false;
    CeProfileSource profiles[] = {
        {ce_cstr(&g->arena,"C11.profile"),ce_builtin_C11Profile()},
        {ce_cstr(&g->arena,"CGNU.profile"),ce_builtin_CGNUProfile()},
        {ce_cstr(&g->arena,"CGNUC11.profile"),ce_builtin_CGNUC11Profile()}
    };
    return ce_profile_bundle(g,profiles,3,profiles[gnu ? 1 : 2].name,error);
}

static CeText dependency_word(CeArena *a, const char *path) {
    size_t n = strlen(path); unsigned char *bytes = ce_alloc(a,n*2+1); size_t out = 0;
    for (size_t i = 0; i < n; ++i) {
        unsigned char c = (unsigned char)path[i];
        if (c == '$') bytes[out++] = '$';
        else if (c == ' ' || c == '\t' || c == '#' || c == '\\') bytes[out++] = '\\';
        bytes[out++] = c;
    }
    return (CeText){bytes,out};
}
static bool dependency_boundary(unsigned c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
static bool dependency_write(CeArena *a, CeText source, FILE *file, char **generated,
                              char **original, size_t len, CeError *error) {
    CeText *before = ce_alloc(a,len*sizeof(*before)), *after = ce_alloc(a,len*sizeof(*after));
    for (size_t i = 0; i < len; ++i) {
        before[i] = dependency_word(a,generated[i]); after[i] = dependency_word(a,original[i]);
    }
    for (size_t pos = 0; pos < source.len;) {
        size_t found = len;
        if (!pos || dependency_boundary(source.data[pos-1])) for (size_t i = 0; i < len; ++i)
            if (before[i].len <= source.len-pos && !memcmp(source.data+pos,before[i].data,before[i].len) &&
                (pos+before[i].len == source.len || dependency_boundary(source.data[pos+before[i].len]))) { found = i; break; }
        if (found < len) {
            fwrite(after[found].data,1,after[found].len,file); pos += before[found].len;
        } else fputc(source.data[pos++],file);
    }
    return !ferror(file) || ce_error(error,0,"cannot finish generated dependency output");
}
static bool dependency_rewrite(CeArena *a, const char *path, char **generated, char **original,
                                size_t len, CeError *error) {
    CeText source;
    if (!ce_read_file(a,path,&source,error)) return false;
    FILE *file = fopen(path,"wb");
    if (!file) return ce_error(error,0,"cannot update generated dependency file: %s",strerror(errno));
    bool ok = dependency_write(a,source,file,generated,original,len,error);
    ok = fclose(file) == 0 && ok;
    return ok || ce_error(error,0,"cannot finish generated dependency file");
}

int ce_source_command(int argc, char **argv, unsigned composition) {
    CeArena arena = {0}; CeGrammar g = {0}; CeError error = {{0},0};
    CeCatalog catalog = {.composition=composition}; CeTokens tokens = {0};
    CeForest forest = {.root=-1}; CeDFA dfa = {0}; CeLR lr = {0}; CeText source = {NULL,0};
    bool module = !strcmp(argv[1],"check-module") || !strcmp(argv[1],"module-grammar");
    bool gnu = true; const char *path = argv[2]; int result = 1;
    if (!module && argc == 4) {
        if (!strcmp(argv[2],"--std=c11")) gnu = false;
        else if (strcmp(argv[2],"--std=gnu11")) {
            ce_error(&error,0,"source command supports --std=c11 or --std=gnu11"); goto done;
        }
        path = argv[3];
    }
    if (module) {
        if (!ce_grammar_read(&g,ce_builtin_TheoryModuleSyntax(),&error) || !ce_grammar_finish(&g,&error)) goto done;
        catalog.syntax = &g; CeModule *loaded; CePresentation presentation;
        if (!ce_read_file(&arena,path,&source,&error) ||
            !ce_module_load(&catalog,path,&loaded,&error) || !ce_module_check(&catalog,loaded,&error)) goto done;
        if (!strcmp(argv[1],"check-module")) puts("module: theory-source admission passed");
        else {
            if (!ce_module_theory(&catalog,loaded,argv[3],&presentation,&error)) goto done;
            for (size_t i = 0; i < presentation.len; ++i) {
                CeTree *tree = presentation.definitions[i].tree;
                if (!ce_tree_print(&g,tree,tree->symbol,stdout,&error)) goto done;
                fputs(";\n",stdout);
            }
        }
    } else {
        if (!ce_builtin_language(&g,gnu,&error) || !ce_read_file(&arena,path,&source,&error)) goto done;
        int entry = g.entries[0]; CeTree *tree;
        if (!strcmp(argv[1],"write")) {
            if (!ce_builtin_decode(&g,source,NULL,&arena,&tree,&error) ||
                !ce_tree_print(&g,tree,entry,stdout,&error)) goto done;
            fputc('\n',stdout);
        } else {
            if (!ce_dfa_prepare(&g,&dfa,&error) || !ce_lr_prepare(&g,entry,&lr,&error) ||
                !ce_dfa_lex(&g,&dfa,source,&tokens,&error) || !ce_lr_parse(&g,&lr,&tokens,&forest,&error) ||
                !ce_tree_from_forest(&g,&forest,&tokens,&arena,&tree,&error)) goto done;
            ce_tree_encode(&g,tree,stdout); fputc('\n',stdout);
        }
    }
    result = ferror(stdout) ? 1 : 0;
done:
    if (result && error.message[0]) fprintf(stderr,"%s: byte %zu: %s\n",path,error.offset,error.message);
    ce_lr_free(&lr); ce_dfa_free(&dfa); ce_forest_free(&forest); ce_tokens_free(&tokens);
    ce_catalog_free(&catalog); ce_grammar_free(&g); ce_arena_free(&arena); return result;
}

int ce_cc(int argc, char **argv) {
    CeArena arena = {0}; CeGrammar g = {0}; CeError error = {{0},0};
    const char *compiler = getenv("CESTA_CC");
    const char *presentation = NULL;
    const char *dependency_path = NULL, *output_path = NULL;
    bool dependencies = false, dependency_only = false, stop_before_link = false;
    if (!compiler || !*compiler) compiler = "cc";
    char **arguments = ce_alloc(&arena,((size_t)argc+5)*sizeof(*arguments));
    unsigned char *input = ce_alloc(&arena,(size_t)argc+5);
    size_t n = 1, sexprs = 0;
    bool gnu = true, standard = false, after_options = false;
    bool supported_standard = true;
    for (int i = 0; i < argc; ++i) {
        if (!after_options && !strcmp(argv[i],"--cc")) {
            if (++i == argc) { ce_error(&error,0,"--cc requires a compiler executable"); goto failed; }
            compiler = argv[i]; continue;
        }
        if (!after_options && !strcmp(argv[i],"--presentation")) {
            if (++i == argc) { ce_error(&error,0,"--presentation requires a versioned key"); goto failed; }
            presentation = argv[i]; continue;
        }
        size_t at = n; arguments[n++] = argv[i];
        if (!after_options && !strcmp(argv[i],"--")) { after_options = true; continue; }
        if (!after_options && !strncmp(argv[i],"-std=",5)) {
            standard = true; gnu = !strncmp(argv[i]+5,"gnu",3);
            supported_standard = !strcmp(argv[i]+5,"c11") || !strcmp(argv[i]+5,"gnu11") || !strcmp(argv[i]+5,"iso9899:2011");
        }
        if (!after_options) {
            if (!strcmp(argv[i],"-MD") || !strcmp(argv[i],"-MMD")) dependencies = true;
            if (!strcmp(argv[i],"-M") || !strcmp(argv[i],"-MM")) dependencies = dependency_only = true;
            if (!strcmp(argv[i],"-c") || !strcmp(argv[i],"-S") || !strcmp(argv[i],"-E")) stop_before_link = true;
            if (!strncmp(argv[i],"-MF",3) && argv[i][3]) dependency_path = argv[i]+3;
            if (!strcmp(argv[i],"-MF") && i+1 < argc) dependency_path = argv[i+1];
            if (!strcmp(argv[i],"-o") && i+1 < argc) output_path = argv[i+1];
            if (!strncmp(argv[i],"-o",2) && argv[i][2]) output_path = argv[i]+2;
        }
        if (!after_options && option_value(argv[i])) {
            if (i+1 == argc) { ce_error(&error,0,"compiler option requires its argument"); goto failed; }
            arguments[n++] = argv[++i]; continue;
        }
        if ((after_options || argv[i][0] != '-') && suffix(argv[i],".csexp")) {
            input[at] = 1; ++sexprs;
        } else if ((after_options || argv[i][0] != '-') && suffix(argv[i],".cesta")) {
            input[at] = 2; ++sexprs;
        }
    }
    arguments[0] = (char *)compiler;
    char **generated_sources = ce_alloc(&arena,sexprs*sizeof(*generated_sources));
    char **original_sources = ce_alloc(&arena,sexprs*sizeof(*original_sources)); size_t source_count = 0;
    if (sexprs) {
        if (!supported_standard) { ce_error(&error,0,"Cesta's current compiler profile supports -std=c11 or -std=gnu11"); goto failed; }
        if (!ce_builtin_language(&g,gnu,&error)) goto failed;
        const char *cache = getenv("CESTA_BUILD_DIR");
        if (!cache || !*cache) cache = "build/cesta-generated";
        char *directory = ce_alloc(&arena,strlen(cache)+1); strcpy(directory,cache);
        if (!directories(directory,&error)) goto failed;
        for (size_t i = 1; i < n; ++i) if (input[i]) {
            const char *source_path = arguments[i]; CeText source; CeTree *tree;
            if (!ce_read_file(&arena,source_path,&source,&error) ||
                !ce_builtin_decode_default(&g,source,presentation,input[i] == 2 ? "compact-c/2" : "ast/1",&arena,&tree,&error)) {
                fprintf(stderr,"%s: byte %zu: %s\n",source_path,error.offset,error.message); goto done_failure;
            }
            uint64_t hash = UINT64_C(14695981039346656037);
            for (const unsigned char *p = (const unsigned char *)source_path; *p; ++p) hash = (hash ^ *p)*UINT64_C(1099511628211);
            const char *base = strrchr(source_path,'/'); base = base ? base+1 : source_path;
            size_t base_len = strlen(base)-6, capacity = strlen(cache)+base_len+40;
            char *parent = ce_alloc(&arena,strlen(cache)+20);
            snprintf(parent,strlen(cache)+20,"%s/%016llx",cache,(unsigned long long)hash);
            if (!directories(parent,&error)) goto failed;
            char *path = ce_alloc(&arena,capacity);
            snprintf(path,capacity,"%s/%.*s.c",parent,(int)base_len,base);
            if (!ce_tree_emit_checked(&g,tree,g.entries[0],path,&error)) {
                fprintf(stderr,"%s: byte %zu: %s\n",source_path,error.offset,error.message); goto done_failure;
            }
            arguments[i] = path;
            generated_sources[source_count] = path; original_sources[source_count++] = (char *)source_path;
        }
        if (!standard) {
            memmove(arguments+2,arguments+1,(n-1)*sizeof(*arguments));
            arguments[1] = "-std=gnu11"; ++n;
        }
    }
    arguments[n] = NULL;
    {
        const char *depfile = dependency_path ? dependency_path : dependency_only ? output_path : NULL;
        bool capture = dependencies && source_count &&
            ((depfile && !strcmp(depfile,"-")) || (dependency_only && !depfile));
        CeText captured; int result;
        if (capture) {
            result = ce_execute_stdout(arguments,&arena,&captured,&error);
            if (!dependency_write(&arena,captured,stdout,generated_sources,original_sources,source_count,&error)) result = 1;
        } else result = ce_execute(arguments,&error);
        if (!result && dependencies && source_count) {
            if (depfile && strcmp(depfile,"-")) {
                if (!dependency_rewrite(&arena,depfile,generated_sources,original_sources,source_count,&error)) result = 1;
            } else if (!capture && !dependency_path) {
                for (size_t i = 0; i < source_count && !result; ++i) {
                    const char *base = strrchr(original_sources[i],'/'); base = base ? base+1 : original_sources[i];
                    const char *object = output_path ? output_path : base;
                    size_t length = strlen(object), stem = output_path ? length : length-6;
                    if (output_path) {
                        const char *dot = strrchr(object,'.');
                        if (dot && (!strrchr(object,'/') || dot > strrchr(object,'/'))) stem = (size_t)(dot-object);
                    }
                    char *path = ce_alloc(&arena,stem+3); memcpy(path,object,stem); memcpy(path+stem,".d",3);
                    if (!output_path && !stop_before_link) {
                        struct stat info;
                        bool found = false;
                        char *fallback = ce_alloc(&arena,stem+5);
                        memcpy(fallback,"a-",2); memcpy(fallback+2,path,stem+3);
                        const char *choices[] = {path,fallback};
                        for (size_t choice = 0; choice < 2 && !result; ++choice) if (!stat(choices[choice],&info)) {
                            found = true;
                            if (!dependency_rewrite(&arena,choices[choice],generated_sources,original_sources,source_count,&error)) result = 1;
                        }
                        if (!found) { ce_error(&error,0,"native compiler produced no default dependency file"); result = 1; }
                        continue;
                    }
                    if (!dependency_rewrite(&arena,path,generated_sources,original_sources,source_count,&error)) result = 1;
                }
            }
        }
        if (result && error.message[0]) fprintf(stderr,"cesta cc: %s\n",error.message);
        ce_grammar_free(&g); ce_arena_free(&arena); return result;
    }
failed:
    fprintf(stderr,"cesta cc: %s\n",error.message);
done_failure:
    ce_grammar_free(&g); ce_arena_free(&arena); return 1;
}
