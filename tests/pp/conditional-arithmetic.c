#if -1 > 0u
unsigned_comparison
#endif
#if 0x7fffffffffffffff + 0 > 0 && (2 || 1/0) && !(0 && 1/0)
short_circuit
#endif
#if defined UNDEFINED_NAME || defined(__STDC__)
defined_forms
#endif
#if UNKNOWN_IDENTIFIER == 0 && 'a' == 97 && '\377' < 0
identifiers_are_zero_char_is_signed
#endif
#if (1 ? 2 : 1/0) == 2 && (0, 1) && 10 % 3 == 1 && -7 / 2 == -3 && (1 << 3) == 8 && ~0 == -1
operators
#endif
#if 0
#garbage that is never read
#if nested
#else
#endif
#elif 1
skipped_groups
#else
not_this
#endif
