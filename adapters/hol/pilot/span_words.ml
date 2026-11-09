(* Spatial and word-list facts used by compiler-output span proofs.
   s2n-bignum is an external dependency; these proofs instantiate its public
   memory model without introducing a new machine semantics. *)

needs "x86/proofs/base.ml";;
needs "common/wordlist.ml";;
needs "x86/proofs/consttime.ml";;

(* The pre-existing event suffix is unrestricted. Only accesses made by the
   current call must lie in its declared source/destination/stack regions. *)
let cesta_copy_trace = new_definition
 `cesta_copy_trace e (x:int64) z n stackpointer s <=>
    ?e2. read events s = APPEND e2 e /\
         memaccess_inbounds e2
           [(x,4 * n); (word_sub stackpointer (word 8),16)]
           [(z,4 * n); (word_sub stackpointer (word 8),8)]`;;

let CESTA_WORDS32 = prove
 (`!a n s. wordlist_from_memory(a,n) s : int32 list =
           wordlist_of_num n (read (memory :> bytes(a,4 * n)) s)`,
  REPEAT GEN_TAC THEN
  MP_TAC(ISPECL [`a:int64`; `n:num`; `s:x86state`]
    (INST_TYPE [`:4`,`:N`] WORDLIST_FROM_MEMORY)) THEN
  REWRITE_TAC[DIMINDEX_4]);;

let CESTA_READ32 = prove
 (`!(xs:int32 list) (a:int64) (s:x86state) i.
      i < LENGTH xs /\ wordlist_from_memory(a,LENGTH xs) s = xs
      ==> read (memory :> bytes32 (word_add a (word (4 * i)))) s = EL i xs`,
  REPEAT STRIP_TAC THEN
  MP_TAC(ISPECL [`a:int64`; `LENGTH(xs:int32 list)`; `i:num`; `s:x86state`]
    (INST_TYPE [`:4`,`:N`] EL_WORDLIST_FROM_MEMORY)) THEN
  ASM_REWRITE_TAC[BYTES32_WBYTES; DIMINDEX_4; EQ_SYM_EQ]);;

let cesta_prefix = new_definition
 `cesta_prefix n (xs:int32 list) = list_of_seq (\i. EL i xs) n`;;

let CESTA_PREFIX_ZERO = prove
 (`!xs:int32 list. cesta_prefix 0 xs = []`,
  REWRITE_TAC[cesta_prefix; list_of_seq]);;

let CESTA_PREFIX_ALL = prove
 (`!xs:int32 list. cesta_prefix (LENGTH xs) xs = xs`,
  REWRITE_TAC[cesta_prefix; LIST_OF_SEQ_EQ_SELF]);;

let CESTA_PREFIX_SUC = prove
 (`!i (xs:int32 list). cesta_prefix (SUC i) xs = APPEND (cesta_prefix i xs) [EL i xs]`,
  REWRITE_TAC[cesta_prefix; list_of_seq]);;

let CESTA_PREFIX_MEMORY32 = prove
 (`!(a:int64) i (xs:int32 list) (s:x86state).
      wordlist_from_memory(a,i) s = cesta_prefix i xs /\
      read (memory :> bytes32 (word_add a (word (4 * i)))) s = EL i xs
      ==> wordlist_from_memory(a,SUC i) s = cesta_prefix (SUC i) xs`,
  REPEAT STRIP_TAC THEN
  MP_TAC(ISPECL [`a:int64`; `i:num`; `s:x86state`]
    (INST_TYPE [`:4`,`:N`] (CONJUNCT2 WORDLIST_FROM_MEMORY_CLAUSES))) THEN
  ASM_REWRITE_TAC[DIMINDEX_4; GSYM BYTES32_WBYTES; CESTA_PREFIX_SUC]);;

let CESTA_LENGTH_SHIFT = prove
 (`!n. n < 2 EXP 61 ==> word_ushr (word n:int64) 61 = word 0`,
  REPEAT STRIP_TAC THEN REWRITE_TAC[GSYM VAL_EQ; VAL_WORD_USHR; VAL_WORD_0] THEN
  ASM_SIMP_TAC[VAL_WORD_EQ; DIMINDEX_64; ARITH_RULE `n < 2 EXP 61 ==> n < 2 EXP 64`;
               DIV_LT]);;

let CESTA_ALIGNED_BYTE = prove
 (`!x:int64. word_and x (word 3) = word 0
      ==> word_and (word_zx x:byte) (word 3) = word 0`,
  CONV_TAC WORD_BLAST);;

let CESTA_ALIGNED_WORD32 = prove
 (`!x:int64. word_and x (word 3) = word 0
      ==> word_and (word_zx x:int32) (word 3) = word 0`,
  CONV_TAC WORD_BLAST);;

let CESTA_STACK_RESTORE = prove
 (`!sp:int64. word_add (word_sub sp (word 8)) (word 8) = sp /\
              word_add (word_sub sp (word 8)) (word 16) = word_add sp (word 8)`,
  CONV_TAC WORD_RULE);;

let CESTA_MASK_MOD4 = prove
 (`!x:N word. val(word_and x (word 3)) = val x MOD 4`,
  GEN_TAC THEN MP_TAC(ISPECL [`x:N word`; `2`] VAL_WORD_AND_MASK_WORD) THEN
  CONV_TAC NUM_REDUCE_CONV THEN REWRITE_TAC[]);;

let CESTA_BYTE_RESIDUE = prove
 (`!x:int64. (word(val x MOD 256):byte) = word_zx x`,
  GEN_TAC THEN REWRITE_TAC[word_zx] THEN
  GEN_REWRITE_TAC RAND_CONV [GSYM WORD_MOD_SIZE] THEN
  REWRITE_TAC[DIMINDEX_8] THEN CONV_TAC NUM_REDUCE_CONV);;

let CESTA_LOW_BYTE_JOIN = prove
 (`!(h:56 word) (b:byte). (word_zx (word_join h b:int64):byte) = b`,
  CONV_TAC WORD_BLAST);;

let CESTA_SUB_ZERO = prove
 (`!(a:N word) b. val(word_sub a b) = 0 <=> val a = val b`,
  REWRITE_TAC[VAL_WORD_SUB_EQ_0; VAL_EQ]);;
