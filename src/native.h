#ifndef CESTA_NATIVE_H
#define CESTA_NATIVE_H

#include "cesta.h"

bool ce_native_document(const CeAtom *atom);
typedef struct { CeText text; CeText *dependencies; size_t ndependencies; } CeNativeOutput;
/* lexer, when present, is lent for printing this grammar across renderings. */
/* plan, when present, receives one record per readable macro use. */
typedef struct { CeText identifiers; bool unguarded, readable; CeLazyLexer *lexer; FILE *plan; } CeNativeEnvironment;
bool ce_native_text_at(const CeGrammar *grammar, CeArena *arena, CeText source,
                       const char *presentation, const char *identity,
                       const CeNativeEnvironment *environment,
                       CeNativeOutput *out, CeError *error);
bool ce_native_text(const CeGrammar *grammar, CeArena *arena, CeText source,
                     const char *presentation, CeText *out, CeError *error);
int ce_expand_command(int argc, char **argv);
int ce_export_c_command(int argc, char **argv);
bool ce_native_prepare(const CeGrammar *grammar, CeArena *arena, CeText source,
                        const char *presentation, const char *identity,
                        size_t argc, char **compiler_arguments, const char *cache,
                        CeNativeOutput *out, CeError *error);
/* As ce_native_prepare, with macro uses written as readable structured C. */
bool ce_native_export(const CeGrammar *grammar, CeArena *arena, CeText source,
                      const char *presentation, const char *identity,
                      size_t argc, char **compiler_arguments, const char *cache, FILE *plan,
                      CeNativeOutput *out, CeError *error);

#endif
