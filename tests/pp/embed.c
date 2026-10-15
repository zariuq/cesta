int a[] = {
#embed "embed/two.bin"
};
int b[] = {
#embed "embed/two.bin" limit(1) prefix(0,) suffix(,9)
};
int c[] = {
#embed "embed/empty.bin" if_empty(42)
};
#if __has_embed("embed/two.bin") == 1 && __has_embed("embed/empty.bin") == 2 && __has_embed("embed/none.bin") == 0
has_embed
#endif
int d[] = {
#embed "embed/hundred.bin"
};
