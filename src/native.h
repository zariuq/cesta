#ifndef CESTA_NATIVE_H
#define CESTA_NATIVE_H

#include "cesta.h"

bool ce_native_document(const CeAtom *atom);
typedef struct { CeText text; CeText *dependencies; size_t ndependencies; } CeNativeOutput;
typedef struct { CeText identifiers; bool unguarded; } CeNativeEnvironment;
bool ce_native_text_at(const CeGrammar *grammar, CeArena *arena, CeText source,
                       const char *presentation, const char *identity,
                       const CeNativeEnvironment *environment,
                       CeNativeOutput *out, CeError *error);
bool ce_native_text(const CeGrammar *grammar, CeArena *arena, CeText source,
                     const char *presentation, CeText *out, CeError *error);
int ce_expand_command(int argc, char **argv);
bool ce_native_prepare(const CeGrammar *grammar, CeArena *arena, CeText source,
                        const char *presentation, const char *identity,
                        size_t argc, char **compiler_arguments, const char *cache,
                        CeNativeOutput *out, CeError *error);

#endif
