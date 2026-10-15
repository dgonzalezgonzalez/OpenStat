clear all
set more off
set obs 4
generate id = _n
generate x = 10*id
save "master.dta", replace
clear
set obs 3
generate id = _n
generate y = 100*id
save "using.dta", replace
use "master.dta", clear
merge 1:1 id using "using.dta"
count if _merge == 3
display "__OPENSTAT__matched=" %21.14g r(N)
count if _merge == 1
display "__OPENSTAT__master_only=" %21.14g r(N)
sort id
display "__OPENSTAT__n=" %21.14g _N
export delimited using "merged.csv", replace
