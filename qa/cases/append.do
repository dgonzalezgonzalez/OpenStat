clear all
set more off
set obs 2
generate id = _n
generate x = id*10
save "first.dta", replace
replace id = id + 2
replace x = id*10
append using "first.dta"
sort id
summarize x
display "__OPENSTAT__n=" %21.14g r(N)
display "__OPENSTAT__mean=" %21.14g r(mean)
export delimited using "appended.csv", replace
