:- initialization(main, main).

main :-
    current_prolog_flag(argv, [Parser, Input]),
    read_wire(Parser, Input, Term),
    garbage_collect,
    emit_wire(Term),
    nl.

read_wire(Parser, Input, Term) :-
    consult(Parser),
    setup_call_cleanup(open(Input, read, Stream), read_string(Stream, _, Source), close(Stream)),
    once(sread(Source, Term)), !.

% Read with PeTTa's actual parser; stream the quoted wire's resulting atom kinds
% independently, without constructing a second whole-document code list.
emit_wire(Term) :-
    is_list(Term), !,
    write('('), emit_sequence(Term), write(')').
emit_wire(Term) :-
    atom(Term), !,
    memberchk(Term, ['cesta-wire/1', 'cesta:e', 'cesta:s', 'cesta:t']),
    format('~a', [Term]).
emit_wire(Term) :-
    string(Term), !,
    string_codes(Term, Codes), maplist(hex_code, Codes),
    format('"~s"', [Codes]).

emit_sequence([]).
emit_sequence([Term|Terms]) :-
    emit_wire(Term), emit_tail(Terms).
emit_tail([]).
emit_tail([Term|Terms]) :-
    write(' '), emit_wire(Term), emit_tail(Terms).

hex_code(Code) :- between(0'0, 0'9, Code), !.
hex_code(Code) :- between(0'a, 0'f, Code).
