"""Typed table boundaries, ordered projections, references and native row exports."""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "build/cesta"
WORK = ROOT / "build/tables"
FIXTURE = ROOT / "tests/tables/Registry.data"


def run(args, ok=True):
    result = subprocess.run(list(map(str, args)), cwd=ROOT, capture_output=True, text=True)
    if (result.returncode == 0) != ok:
        raise AssertionError(f"{args}: {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    source = FIXTURE.read_text()
    normalized = run([BINARY, "tables", "check", FIXTURE]).stdout
    path = WORK / "normalized.data"; path.write_text(normalized)
    assert run([BINARY, "tables", "check", path]).stdout == normalized
    projection = run([BINARY, "tables", "rows", path, "Profiles", "label", "id"]).stdout
    assert '(Row "he" 7) (Row "extended" 19)' in projection
    assert "(Columns label id)" in projection
    out = WORK / "rows.h"
    run([BINARY, "tables", "header", path, "-o", out])
    before = out.read_bytes(); run([BINARY, "tables", "header", path, "-o", out]); assert before == out.read_bytes()
    mutations = {
        "duplicate-table": source.replace('(Table Languages', '(Table Profiles', 1),
        "duplicate-column": source.replace('(Column code Identifier)', '(Column id Identifier)'),
        "missing-primary": source.replace('(Key id)', '(Unique id)', 1),
        "empty-key": source.replace('(Key id)', '(Key)', 1),
        "duplicate-key-column": source.replace('(Key id)', '(Key id id)', 1),
        "missing-key-column": source.replace('(Key id)', '(Key absent)', 1),
        "wrong-arity": source.replace('(Row 7 he he', '(Row 7 he'),
        "wrong-bool": source.replace('true pure', 'maybe pure'),
        "wrong-text": source.replace('"HE"', 'HE'),
        "wrong-enum": source.replace('true pure', 'true absent'),
        "empty-enum": source.replace('(Enum pure reads writes unknown)', '(Enum)'),
        "duplicate-enum": source.replace('(Enum pure reads writes unknown)', '(Enum pure pure)'),
        "leading-zero": source.replace('(Row 7 ', '(Row 07 '),
        "negative-id": source.replace('(Row 7 ', '(Row -1 '),
        "overflow-id": source.replace('(Row 7 ', '(Row 18446744073709551616 '),
        "repeated-id": source.replace('(Row 19 ', '(Row 7 '),
        "repeated-unique": source.replace('extended petta "extended"', 'extended he "he"'),
        "dangling-reference": source.replace('(Row 7 he he ', '(Row 7 he absent '),
        "unknown-target": source.replace('(Ref Languages id)', '(Ref Missing id)'),
        "unknown-target-column": source.replace('(Ref Languages id)', '(Ref Languages absent)'),
        "non-key-target": source.replace('(Ref Languages id)', '(Ref Languages label)'),
        "dense-sparse-ids": source.replace('(Key id) (Unique language label)', '(Key id) (Unique language label) (Dense id)'),
        "dense-text": source.replace('(Key id) (Unique label)', '(Key id) (Unique label) (Dense id)'),
        "extra-section": source.replace('(Key id) (Unique label)', '(Key id) (Unique label) (Bogus)'),
        "extra-document": source + '\n(cesta-tables/1)\n',
    }
    for name, data in mutations.items():
        bad = WORK / (name + ".data"); bad.write_text(data)
        run([BINARY, "tables", "check", bad], False)
        out.write_bytes(before)
        run([BINARY, "tables", "header", bad, "-o", out], False)
        assert out.read_bytes() == before, name + " overwrote output after refusal"
    run([BINARY, "tables", "rows", path, "Profiles", "missing"], False)
    edge = WORK / "edge.data"
    edge.write_text('''(cesta-tables/1
      (Table Flags (Columns (Column flag Bool)) (Key flag) (Rows (Row true) (Row false)))
      (Table Text (Columns (Column key Text)) (Key key) (Rows (Row "a\\x01f??=é")))
      (Table Edges (Columns (Column id Nat) (Column symbol Identifier) (Column text (Ref Text key))
                            (Column flag (Ref Flags flag))) (Key id)
        (Rows (Row 18446744073709551615 cesta_table_apply_0 "a\\x01f??=é" true))))''')
    run([BINARY, "tables", "header", edge, "-o", WORK / "edge.h"])
    spec = WORK / "registry.export"
    spec.write_text('(cesta-export/1 (header REGISTRY_H) (profile c11) '
                    '(table-enum "normalized.data" Profiles (enum ProfileId) (prefix PROFILE_) (id id) (name code)) '
                    '(table-rows "normalized.data" Profiles (macro ENABLED_PROFILES) '
                    '(columns id code label) (where enabled true)))\n')
    run([BINARY, "export-header", "--plan", WORK / "registry.plan", spec, "-o", WORK / "registry.h"])
    projection_source = WORK / "projection.c"
    projection_source.write_text('#include "registry.h"\n'
        '_Static_assert(PROFILE_he == 7 && PROFILE_extended == 19, "IDs changed");\n'
        '#define SUM_ID(id, code, label) + id\n'
        '_Static_assert((0 ENABLED_PROFILES(SUM_ID)) == UINT64_C(7), "Nat row changed");\n'
        '#define ROW(id, code, label) + 1\n'
        'enum { enabled = 0 ENABLED_PROFILES(ROW) };\n'
        'int main(void) { return enabled != 1; }\n')
    for cc in ("gcc", "clang"):
        exe = WORK / ("projection-" + cc)
        run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", projection_source, "-o", exe]); run([exe])
    for old, new in [('(where enabled true)', '(where enabled maybe)'),
                     ('(columns id code label)', '(columns absent)'),
                     ('(id id)', '(id absent)'),
                     ('(enum ProfileId)', '(enum ProfileId) (enum Other)'),
                     ('(enum ProfileId)', '(enum ProfileId) (count PROFILE_he)'),
                     ('(enum ProfileId)', '(enum if)'),
                     ('(enum ProfileId)', '(enum PROFILE_he)'),
                     ('(enum ProfileId)', '(enum ProfileId) (count ProfileId)'),
                     ('(enum ProfileId)', '(enum ProfileId) (where code absent)'),
                     ('(macro ENABLED_PROFILES)', '(macro if)'),
                     ('(macro ENABLED_PROFILES)', '(macro ENABLED_PROFILES) (count IGNORED)'),
                     ('(columns id code label) (where enabled true)', '(columns absent) (where enabled false)')]:
        bad = WORK / "bad.export"; bad.write_text(spec.read_text().replace(old,new))
        run([BINARY, "export-header", bad, "-o", WORK / "bad.h"], False)
    empty_spec = WORK / "empty.export"
    empty_spec.write_text(spec.read_text().replace('(enum ProfileId)',
        '(enum ProfileId) (where code absent) (count EMPTY_COUNT)'))
    run([BINARY, "export-header", empty_spec, "-o", WORK / "empty.h"])
    (WORK / "empty.c").write_text('#include "empty.h"\nint main(void) { return EMPTY_COUNT; }\n')
    for cc in ("gcc", "clang"):
        exe = WORK / ("empty-" + cc)
        run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", WORK / "empty.c", "-o", exe]); run([exe])
    duplicate_spec = WORK / "duplicate.export"
    duplicate_spec.write_text(spec.read_text().replace('(macro ENABLED_PROFILES)', '(macro PROFILE_he)'))
    run([BINARY, "export-header", duplicate_spec, "-o", WORK / "duplicate.h"], False)
    empty_data = WORK / "empty-names.data"
    empty_data.write_text('(cesta-tables/1 (Table Empty (Columns (Column id Nat) (Column name Text)) '
                          '(Key id) (Dense id) (Rows)))\n')
    for options in ('(array names) (id id) (text name) (count COUNT)',
                    '(array names) (id id) (count COUNT)',
                    '(array names) (text name) (count COUNT)'):
        names_spec = WORK / "empty-names.export"
        names_spec.write_text('(cesta-export/1 (header NAMES_H) (profile c11) '
                              '(table-names "empty-names.data" Empty ' + options + '))\n')
        run([BINARY, "export-header", names_spec, "-o", WORK / "empty-names.h"], False)
    keywords = WORK / "keywords.data"
    keywords.write_text('(cesta-tables/1 (Table Keywords (Columns (Column id Nat) '
                        '(Column code Identifier)) (Key id) (Rows (Row 7 if))))\n')
    keyword_spec = WORK / "keywords.export"
    keyword_spec.write_text('(cesta-export/1 (header KEYWORDS_H) (profile c11) '
                           '(table-enum "keywords.data" Keywords (enum Keyword) (id id) (name code)))\n')
    # Logical names may be keywords. Their final C declarations need a prefix.
    run([BINARY, "tables", "check", keywords])
    run([BINARY, "export-header", keyword_spec, "-o", WORK / "keywords.h"], False)
    keyword_spec.write_text(keyword_spec.read_text().replace('(enum Keyword)', '(enum Keyword) (prefix SAFE_)'))
    run([BINARY, "export-header", keyword_spec, "-o", WORK / "keywords.h"])
    keyword_c = WORK / "keywords.c"
    keyword_c.write_text('#include "keywords.h"\nint main(void) { return SAFE_if != 7; }\n')
    for cc in ("gcc", "clang"):
        exe = WORK / ("keywords-" + cc)
        run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", keyword_c, "-o", exe]); run([exe])
    # The generated row formal must not capture a user identifier with its default name.
    harness = WORK / "native.c"
    harness.write_text('''#include "edge.h"
#include <string.h>
#define CELL(id, symbol, text, flag) static const unsigned char payload[] = text; \\
    _Static_assert(id == UINT64_MAX, "explicit id changed"); \\
    _Static_assert(flag == 1, "reference scalar type changed"); \\
    static const char name[] = #symbol;
CESTA_TABLE_Edges_ROWS(CELL)
int main(void) {
    static const unsigned char expected[] = {97, 1, 102, 63, 63, 61, 195, 169, 0};
    return sizeof(payload) != sizeof(expected) || memcmp(payload, expected, sizeof(expected)) ||
           strcmp(name, "cesta_table_apply_0");
}
''')
    builds = []
    for cc in ("gcc", "clang"):
        for level in ("-O0", "-O2"):
            exe = WORK / (cc + level)
            run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", level, harness, "-o", exe])
            run([exe]); builds.append(cc + level)
    (WORK / "summary.json").write_text(json.dumps({"refusals":len(mutations)+1,"builds":builds,
        "checks":["roundtrip", "ordered projections", "sparse IDs", "references", "formal hygiene", "C literal bytes", "refusal preserves output"]},indent=2)+"\n")
    print(f"typed tables: {len(mutations)+1} refusals; ordered projection, references, explicit uint64 IDs, C literal bytes and macro hygiene passed in {len(builds)} builds")


if __name__ == "__main__":
    main()
