!(import! &csource program.metta)

(= (c-nodes $node) $node)
(= (c-nodes $node)
   (if (is-expr $node)
       (let $children (cdr-atom $node)
            (c-nodes (superpose $children)))
       (empty)))

(= (c-function-name
     (c:ExternalFunction
       (c:FunctionDefinition $spec
         (c:Declarator (c:FunctionDeclarator (c:NameDeclarator $name) $params))
         $old $body)))
   $name)
(= (c-call-name (c:Call (c:Var $name) $arguments)) $name)
(= (c-variable-name (c:Var $name)) $name)
(= (c-literal-spelling (c:IntConst $spelling)) $spelling)

!(test (sum main)
       (collapse (c-function-name
         (c-nodes (match &csource (c:TranslationUnit $declarations) $declarations)))))
!(test (sum)
       (collapse (c-call-name
         (c-nodes (match &csource (c:TranslationUnit $declarations) $declarations)))))
!(test (x y sum)
       (collapse (c-variable-name
         (c-nodes (match &csource (c:TranslationUnit $declarations) $declarations)))))
!(test ("3u" "4u")
       (collapse (c-literal-spelling
         (c-nodes (match &csource (c:TranslationUnit $declarations) $declarations)))))
!(test ()
       (collapse (let $name
         (c-variable-name
           (c-nodes (match &csource (c:TranslationUnit $declarations) $declarations)))
         (if (== $name imaginaryVariable) $name (empty)))))

!(println! (match &csource (c:TranslationUnit $declarations)
                         (c:TranslationUnit $declarations)))
