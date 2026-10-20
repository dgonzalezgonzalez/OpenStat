* c01: core language - comments, delimiters, continuations, macros, strings
clear all
set more off
* a comment
// another comment
/* block
   comment */
local a 10
local b `a' * 2
display "b=" `b'
global g 5
local c `b' + $g
display "c=" `c'
#delimit ;
display "semi1" ;
display "semi2" ;
#delimit cr
display "line1"
display "line2"
display "cont=" 1 + ///
    2
display "quoted" " " "text"
display "instr=" 6/2
display "exp=" 2^3
display "mod=" mod(7,3)
display "__OPENSTAT__a=" %21.14g `a'
display "__OPENSTAT__b=" %21.14g `b'
display "__OPENSTAT__c=" %21.14g `c'
display "__OPENSTAT__g=" %21.14g $g
