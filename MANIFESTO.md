# Cesta

*Cesta is Czech and Slovak for path, way, journey: the way from C as it is to C that says what it means and
proves what it claims, without ever leaving C.*

## The starting point

C gives programmers direct access to storage layout, pointers, ordinary control flow and inline assembly.
It is widely used for operating systems, runtimes and native interfaces. A mind that wants to work with reality,
human or artificial, needs that access directly and at full speed, not through a translation step.

C also hides what is happening:

- the preprocessor is text substitution outside the language, untyped and unhygienic;
- undefined behaviour lets the optimizer assume what the code never states;
- conversions and promotions happen silently;
- ownership, lifetimes, provenance and aliasing live only in the programmer's head;
- arrays lose their length, `void *` loses its type, errors hide in return codes;
- layout, padding and endianness are left to the implementation;
- the build (include order, flags, ABI) decides meaning from outside the source;
- a program cannot see its own structure.

Other languages answer this by asking you to leave C. Cesta answers it by improving C, one region at a time,
so that what you gain never costs what C gives you.

## Principles

1. **C stays valid.** Any C program written normally is a Cesta program, and GCC and Clang build it. Cesta
   enriches C additively. Whatever it adds must lower to ordinary C.

2. **The machine is ambient; types are claims.** Types, ownership and permissions are claims about regions
   of the machine. Ownership checking adds no runtime borrow ledger. Explicit bounds and arithmetic
   checks remain visible operations with ordinary C costs; their use is optional.

3. **Structure grows inward.** Unchecked is the default. A checked region states what it guarantees, what it
   may touch and what it trusts. Claims are earned region by region; Cesta does not attempt to verify C in general. Where
   code steps outside a checked region, it names the obligation it relies on.

4. **One tree, many faces.** Every way of writing Cesta (C text, compact S-expressions, other presentations)
   can use the shared C syntax machinery. Lossless source retention and structured pretty-printing have
   separate round-trip contracts. Code is data: a program can be read,
   queried and rewritten like any other structure, then printed as C.

5. **One language at the compiler.** `cesta build` compiles a whole project through GCC or Clang. Errors,
   `__LINE__`, assertions and debuggers point at Cesta source, not at generated files.

6. **There is always a way out.** Cesta exports readable, ordinary C that builds byte-identically to its own
   build. Nobody is locked in, and nobody has to trust Cesta to read what it produced. The goal is clean,
   structured C that a careful person would have written, never machine slop.

7. **Declare once.** A layout, a table, an operation's contract, a protocol or an interface is declared one
   time. The checker, the proofs, the generated C and the runtime's rows all read that one declaration, so
   they cannot drift apart.

8. **Macros are language.** Structured templates have typed arguments, explicit evaluation rules and
   binding identities. Their admitted hygiene boundary is stated; arbitrary native preprocessor programs
   remain outside it. Expansion produces ordinary C.

9. **Add useful abstractions.** Optional ownership, borrowed views, resource protocols and reusable
   declarations make obligations explicit. Each supported fragment has concrete limits. Records and
   operations should compose without imposing an object runtime or hiding allocation policy.

10. **Evidence before claims.** Every claim is checked against something independent of Cesta: GCC and
    Clang, a proof checker's kernel, the original program being translated, differential tests on
    generated inputs, and deliberate mutations that a test must catch. A port is equal to its original, or
    the difference is named. When the evidence can be read two ways, the answer is "inconclusive", never a
    guess. Behaviour is carried over as observed, even when a comment says otherwise.

11. **Theory in step with code.** C's syntax is a grammar with laws; C's types are derived from C's own
    rules; ownership and macro additions have scoped mathematical models. Selected machine artifacts can
   have independent HOL proofs. No general executable IR or C interpreter is a prerequisite. Theory earns
    its place by catching real errors, never by making the compiler a monument to itself.

12. **The road goes on.** C is the execution base of a larger language: one in which a mind can reason in
    high-level forms and act on the machine in the same language, with exact routes between the two. Other
    languages become modes and surfaces over the same ground, not walls in front of it.

## Where Cesta stands

What exists and is checked today:

- **Real C, read and printed exactly.** A grammar derived rule by rule from the C11 standard plus the GNU
  dialect. Large real code bases read, print back, and compile to byte-identical object files.
- **C's own meaning.** Every name bound to what it denotes and every expression given its C11 type, value
  category and implicit conversions, checked against GCC and Clang on about two million expressions.
- **Authoring.** Compact S-expression presentations of C, with includes, defines and conditionals kept as
  first-class forms.
- **Structured typed macros**, with libraries for checked narrowing, worklists, sizes and probes.
  Binding and expansion laws have separate models; native expansion and printing have their own tests.
- **Record families:** one declaration gives a record type and its functions for a chosen element type,
  with member access tied to the record that declares the member. A worklist and a read cursor exist,
  each compiling to the instructions of the hand-written declarations.
- **Declarations that generate code:** machine-word layouts admitted by a proof checker, typed tables,
  operation contracts and executable protocols.
- **A checked fragment** for byte buffers, whose ownership permissions are admitted by proofs.
- **Ownership over ordinary C:** borrowed and owning references, views, record fields, output pointers and
  callbacks with declared targets, checked in native C function bodies with no runtime bookkeeping.
  Unsupported constructs are refused, and unchecked definitions are named with their obligations.
- **One-language builds:** project files, incremental and parallel builds, diagnostics and debug lines in
  Cesta source, and a plain-C export with its own Makefile.

## The proving ground

A systems language proves itself by building a system. SexOS is an open-source microkernel operating
system written in Rust. Cesta is translating all of it, component by component, with the Rust kept as the
reference:

- each component's Cesta build must match the Rust on differential traces over generated scenarios, under
  both compilers and with sanitizers, and source mutations must be caught;
- its plain-C export must build byte-identically;
- the whole image must boot with each component swapped in, and behave as the reference does.

The Cesta kernel and current joined userland have boot evidence, while newer upstream behavior and
long-running lifecycle work remain active. The port's own coverage and qualification records state the
current scope; historical source counts are not a completion measure.

## What Cesta will not do

- break a C program that a C compiler accepts;
- require a runtime borrow ledger, garbage collector or proof checker for ordinary builds;
- force one syntax on anyone;
- hide the machine behind an abstraction it cannot see through;
- claim a guarantee it has not earned, or call a translation checked when it is only faithful;
- keep anyone from walking away with plain C.

## The way

Start from C as it is. Make it legible. Carve out regions that say what they mean and prove it. Grow those
regions until the checked part is the part that matters. Build real systems the whole way, because a
language is only as honest as the largest thing built with it.
