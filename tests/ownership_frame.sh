#!/bin/sh
# The frame rule (ownership/INTERFACE.txt, FRAME): an internal function's
# interface lists the record fields its body or its callees reach, and the byte
# ranges of a borrowed record's own storage it reaches, each read or written;
# an external function keeps every field and its whole pointee. Lean admission
# of the examples is the ownership admission gate's, which admits every
# ownership/examples/*.cesta.
set -u
cesta=${1:-build/cesta}
dir=ownership/examples
work=$(mktemp -d build/ownership-frame.XXXXXX) || exit 1
trap 'rm -rf "$work"' EXIT
fail=0
for example in Frame FrameBytes; do
    "$cesta" export-c "$dir/$example.cesta" --std=c11 -o "$work/$example.c" --plan "$work/$example.plan" >"$work/out" 2>&1 ||
        { echo "FAIL: $example.cesta refused: $(head -1 "$work/out")"; exit 1; }
done
expect() {   # EXAMPLE NAME SIGNATURE-PARAMETERS
    got=$(perl -ne 'while (/\(function ([a-z_0-9]+) \(signature \(parameters(.*?)\) \(variadic/g) { print "$2\n" if $1 eq "'"$2"'"; }' "$work/$1.plan")
    [ "$got" = "$3" ] || { echo "FAIL: $2's interface is ($got), expected ($3)"; fail=1; }
}
# a's pointer bytes and its target; a and b; nothing at all; an external function: everything
expect Frame first   " (borrowed 8 read) (borrowed 4 read)"
expect Frame forward " (borrowed 8 read) (borrowed 4 read)"
expect Frame both    " (borrowed 8 read) (borrowed 8 read) (borrowed 4 read) (borrowed 4 read)"
expect Frame none    ""
expect Frame api     " (borrowed 16 read) (borrowed 4 read) (borrowed 4 read)"
# key written, in the body, through a callee and through recursion; fired[1] read; external: whole
expect FrameBytes bump_key   " (borrowed 4 write)"
expect FrameBytes bump_twice " (borrowed 4 write)"
expect FrameBytes count_down " (borrowed 4 write) (value)"
expect FrameBytes read_fired " (borrowed 4 read)"
expect FrameBytes run        " (borrowed 20 write)"
[ "$fail" = 0 ] && echo "ownership frame rule: 10 interfaces list exactly the fields and bytes their functions reach"
exit "$fail"
