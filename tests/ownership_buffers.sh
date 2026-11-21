#!/bin/sh
# ownership_buffers.sh [CESTA]: counted buffers (ownership/INTERFACE.txt,
# COUNTED BUFFERS). The accepted program is admitted under each available C
# compiler and its plan uses whole units (cesta-ownership/4); each refused
# program is refused for the reason ownership/examples/CountedBuffers/
# refusals.txt gives it. Lean admission of the example program is the
# ownership admission gate's, which admits every ownership/examples/*.cesta.
set -u
cesta=${1:-build/cesta}
dir=ownership/examples/CountedBuffers
mkdir -p build
work=$(mktemp -d build/ownership-buffers.XXXXXX) || exit 1
trap 'rm -rf "$work"' EXIT
fail=0 compilers=0 refusals=0
accepted="accepted dynamic_accepted record_view grow_rehash grow_realloc grow_flag_push grow_push_slot"
# allocations known null own nothing; these hold no whole input, so any plan version
plain="null_owner_return null_owner_join null_owner_moved"
for cc in gcc clang; do
    command -v "$cc" >/dev/null 2>&1 || continue
    compilers=$((compilers + 1))
    for program in $accepted $plain; do
        if ! "$cesta" export-c "$dir/$program.cesta" --cc "$cc" --std=c11 -o "$work/$program.c" \
                --plan "$work/$program.plan" -- -I"$dir" >"$work/out" 2>&1; then
            echo "FAIL: $program refused under $cc: $(head -1 "$work/out")"; fail=1; continue
        fi
        case " $plain " in *" $program "*) continue ;; esac
        head -1 "$work/$program.plan" | grep -q '"cesta-ownership/4"' ||
            { echo "FAIL: $program's plan is not cesta-ownership/4 under $cc"; fail=1; }
    done
done
[ "$compilers" -gt 0 ] || { echo "FAIL: no C compiler"; exit 1; }
tab=$(printf '\t')
while IFS="$tab" read -r name reason; do
    [ -n "$name" ] || continue
    refusals=$((refusals + 1))
    if "$cesta" export-c "$dir/$name.cesta" --std=c11 -o "$work/$name.c" -- -I"$dir" >"$work/out" 2>&1; then
        echo "FAIL: $name was admitted"; fail=1
    elif ! grep -qF "$reason" "$work/out"; then
        echo "FAIL: $name was refused for another reason: $(head -1 "$work/out")"; fail=1
    fi
done < "$dir/refusals.txt"
[ "$fail" = 0 ] && echo "ownership counted buffers, dynamic allocations and null owners: $(echo $accepted $plain | wc -w) programs accepted under $compilers compilers; $refusals programs refused for their reasons"
exit "$fail"
