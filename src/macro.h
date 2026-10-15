#ifndef CESTA_MACRO_H
#define CESTA_MACRO_H

#include "cesta.h"

typedef struct CeMacros CeMacros;
CeMacros *ce_macros_new(const CeGrammar *grammar, const CeAtom *source, CeError *error);
void ce_macros_environment(CeMacros *macros, CeText identifiers, bool unguarded);
void ce_macros_free(CeMacros *macros);
bool ce_macros_load(CeMacros *macros, const char *path, const char *theory, CeError *error);
bool ce_macros_accepts(const CeMacros *macros, const CeAtom *atom, int sort);
unsigned ce_macros_expand(CeMacros *macros, const CeAtom *atom, int sort, int parent,
                          CeArena *arena, CeTree **out, CeAtomDecoder recurse,
                          void *context, CeError *error, size_t depth);
bool ce_macros_metadata(const CeMacros *macros, FILE *file, CeError *error);
size_t ce_macros_dependencies(const CeMacros *macros);
CeText ce_macros_dependency(const CeMacros *macros, size_t index);
size_t ce_macros_rules(const CeMacros *macros);
bool ce_macros_prelude(CeMacros *macros, size_t begin, size_t end, CeArena *arena,
                       CeText *out, CeError *error);
int ce_macro_command(int argc, char **argv);

#endif
