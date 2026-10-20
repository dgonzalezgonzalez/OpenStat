* c05: labels, formats, encode/decode, destring/tostring
clear all
set more off
set obs 4
generate x = _n
label variable x "Explanatory"
label data "Test dataset"
label define lbl 1 "One" 2 "Two" 3 "Three"
label values x lbl
label list lbl
generate str8 s = "42"
destring s, generate(sn)
summarize sn
display "__OPENSTAT__destring=" %21.14g r(mean)
tostring sn, generate(st)
list st
generate str8 cat = "b"
generate str8 cat2 = "a"
encode cat, generate(cate)
list cate
decode cate, generate(catd)
list catd
