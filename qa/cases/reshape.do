clear all
set more off
set obs 3
generate id = _n
generate x1 = id*10
generate x2 = id*10 + 1
reshape long x, i(id) j(period)
sort id period
display "__OPENSTAT__long_n=" %21.14g _N
display "__OPENSTAT__last_x=" %21.14g x[6]
reshape wide x, i(id) j(period)
sort id
display "__OPENSTAT__wide_n=" %21.14g _N
display "__OPENSTAT__x2=" %21.14g x2[3]
export delimited using "reshaped.csv", replace
