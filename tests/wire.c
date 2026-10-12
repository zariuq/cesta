#include "../src/cesta.h"

#include <stdlib.h>

static unsigned checks;
static void require(bool condition, const char *message) {
    ++checks;
    if (!condition) { fprintf(stderr,"wire: %s\n",message); exit(1); }
}
static void roundtrip(CeArena *arena, const CeAtom *original) {
    CeAtom *wire, *parsed, *restored; CeError error = {{0},0};
    require(ce_wire_pack(arena,original,&wire,&error),error.message);
    FILE *file = tmpfile(); require(file != NULL,"wire stream");
    ce_atom_write(wire,file); long length = ftell(file); require(length >= 0,"measure wire");
    rewind(file); CeText bytes = {ce_alloc(arena,(size_t)length+1),(size_t)length};
    require(fread(bytes.data,1,bytes.len,file) == bytes.len,"read wire"); fclose(file);
    for (size_t i = 0; i < bytes.len; ++i) require(bytes.data[i] >= 32 && bytes.data[i] < 127,"wire has only printable ASCII bytes");
    require(ce_atom_read(arena,bytes,&parsed,&error) && ce_wire_unpack(arena,parsed,&restored,&error),error.message);
    require(ce_atom_equal(original,restored),"wire preserves atom kinds, exact spellings and ordered structure");
}
int main(void) {
    CeArena arena = {0}; unsigned char bytes[256];
    for (size_t i = 0; i < sizeof(bytes); ++i) {
        bytes[i] = (unsigned char)i;
        roundtrip(&arena,ce_atom_new(&arena,CE_ATOM_TEXT,ce_text(&arena,bytes+i,1),0));
    }
    CeAtom *values = ce_atom_new(&arena,CE_ATOM_LIST,(CeText){NULL,0},3);
    CeError error = {{0},0};
    require(ce_atom_read(&arena,ce_cstr(&arena,"(PI EXP True False 001 1u 1.0 + λ)"),&values->items[0],&error),error.message);
    values->items[1] = ce_atom_new(&arena,CE_ATOM_TEXT,ce_text(&arena,bytes,sizeof(bytes)),0);
    values->items[2] = ce_atom_new(&arena,CE_ATOM_LIST,(CeText){NULL,0},0);
    roundtrip(&arena,values);
    FILE *original = fopen("build/wire-original.atom","wb");
    FILE *transport = fopen("build/wire-transport.metta","wb");
    CeAtom *wire;
    require(original && transport && ce_wire_pack(&arena,values,&wire,&error),"reader qualification fixtures");
    ce_atom_write(values,original); fputc('\n',original); fclose(original);
    ce_atom_write(wire,transport); fputc('\n',transport); fclose(transport);
    const char *bad[] = {
        "(cesta-wire/2 (cesta:e))", "(cesta-wire/1)", "(cesta-wire/1 (cesta:e) (cesta:e))",
        "(cesta-wire/1 (unknown))", "(cesta-wire/1 (cesta:t \"0\"))", "(cesta-wire/1 (cesta:t \"FF\"))",
        "(cesta-wire/1 (cesta:t \"gg\"))", "(cesta-wire/1 (cesta:s \"\"))",
        "(cesta-wire/1 (cesta:s \"2020\"))", "(cesta-wire/1 (cesta:t 00))",
        "(cesta-wire/1 (cesta:e (cesta:t \"00\") (bad)))"
    };
    for (size_t i = 0; i < sizeof(bad)/sizeof(*bad); ++i) {
        CeAtom *parsed, *restored;
        require(ce_atom_read(&arena,ce_cstr(&arena,bad[i]),&parsed,&error),error.message);
        require(!ce_wire_unpack(&arena,parsed,&restored,&error) && error.message[0],"malformed byte wire is refused with a position");
    }
    printf("portable atom wire: all 256 bytes, scalar kinds, Unicode, reserved names and negative controls; %u checks\n",checks);
    ce_arena_free(&arena); return 0;
}
