#include "atom.h"
#include "parser.h"
#include "symbol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 2 && !(argc == 3 && !strcmp(argv[2],"--dump"))) return 2;
    FILE *file = fopen(argv[1],"rb");
    if (!file || fseek(file,0,SEEK_END)) return 2;
    long size = ftell(file);
    if (size < 0 || fseek(file,0,SEEK_SET)) { fclose(file); return 2; }
    char *text = malloc((size_t)size+1);
    if (!text) { fclose(file); return 2; }
    size_t got = fread(text,1,(size_t)size,file);
    fclose(file);
    if (got != (size_t)size) { free(text); return 2; }
    text[got] = 0;
    Arena arena; SymbolTable symbols; VarInternTable variables;
    arena_init(&arena); symbol_table_init(&symbols);
    symbol_table_init_builtins(&symbols,&g_builtin_syms);
    var_intern_init(&variables); g_symbols = &symbols; g_var_intern = &variables;
    size_t position = 0;
    Atom *atom = parse_sexpr(&arena,text,&position);
    bool accepted = atom && atom->kind == ATOM_EXPR && parser_rest_is_delimiters(text,&position) && position == got;
    if (!accepted) fprintf(stderr,"CeTTa reader refused data at byte %zu\n",position);
    else if (argc == 3) puts(atom_to_parseable_string(&arena,atom));
    g_var_intern = NULL; g_symbols = NULL;
    var_intern_free(&variables); symbol_table_free(&symbols); arena_free(&arena); free(text);
    return accepted ? 0 : 1;
}
