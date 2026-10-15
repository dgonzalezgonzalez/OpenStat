clear all
set more off
set obs 4
generate id = _n
generate treatment = mod(id,2)
generate outcome = 1.25*id
label variable outcome "Synthetic outcome"
label define treatment_label 0 "Control" 1 "Treated"
label values treatment treatment_label
saveold "roundtrip.dta", version(14) replace
clear
use "roundtrip.dta", clear
summarize outcome
display "__OPENSTAT__n=" %21.14g r(N)
display "__OPENSTAT__mean=" %21.14g r(mean)
export delimited using "roundtrip.csv", replace
