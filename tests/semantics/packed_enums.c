enum __attribute__((packed)) leading { LEAD_A, LEAD_B };
enum trailing { TRAIL_A, TRAIL_B } __attribute__((packed));
enum __attribute__((packed)) anonymous_with_comma { COMMA_A, COMMA_B, };
enum __attribute__((packed)) __attribute__((aligned(8))) combined { COMBINED_A, COMBINED_B };
enum __attribute__((aligned(8))) __attribute__((packed)) aligned_first { FIRST_A, FIRST_B };
enum unsigned_byte { BYTE_HIGH = 255 } __attribute__((packed));
enum unsigned_short { SHORT_HIGH = 256 } __attribute__((packed));
enum signed_byte { SIGNED_BYTE_LOW = -128, SIGNED_BYTE_HIGH = 127 } __attribute__((packed));
enum signed_short { SIGNED_SHORT_LOW = -129, SIGNED_SHORT_HIGH = 128 } __attribute__((packed));
enum unsigned_word { WORD_HIGH = 4294967295ULL } __attribute__((packed));
enum signed_wide { WIDE_LOW = -2147483649LL } __attribute__((packed));
enum unsigned_wide { WIDE_HIGH = 18446744073709551615ULL } __attribute__((packed));
enum dependent_alignment { ALIGNMENT_VALUE = 8 } __attribute__((packed, aligned(ALIGNMENT_VALUE)));
enum ordinary { ORDINARY_A, ORDINARY_B };
struct enum_record {
    char before;
    enum leading small;
    enum signed_short medium;
    enum unsigned_wide large;
    char after;
};
int layouts(void) {
    return sizeof(enum leading) + _Alignof(enum leading) + sizeof(enum trailing) +
        sizeof(enum unsigned_byte) + sizeof(enum unsigned_short) + sizeof(enum signed_byte) +
        sizeof(enum signed_short) + sizeof(enum unsigned_word) + sizeof(enum signed_wide) +
        sizeof(enum unsigned_wide) + _Alignof(enum combined) + _Alignof(enum dependent_alignment) +
        sizeof(enum aligned_first) + _Alignof(enum aligned_first) +
        sizeof(enum ordinary) + sizeof(struct enum_record);
}
enum mixed_unsigned_range { mixed_u_small = 8u, mixed_u_large = 0x80000000u };
enum mixed_signed_range { mixed_s_small = 3, mixed_s_negative = -1, mixed_s_large = 0x80000000u };
enum earlier_initializer_types { earlier_small = 1, earlier_next = earlier_small + 1, earlier_large = 0x80000000u };
