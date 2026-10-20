* c07: reshape/collapse/expand/contract
clear all
set more off
set obs 4
generate id = _n
generate x1 = id*10
generate x2 = id*10+1
generate x3 = id*10+2
reshape long x, i(id) j(period)
display "__OPENSTAT__long_n=" %21.14g _N
display "__OPENSTAT__long_x6=" %21.14g x[6]
reshape wide x, i(id) j(period)
display "__OPENSTAT__wide_n=" %21.14g _N
display "__OPENSTAT__wide_x3=" %21.14g x3[3]
clear
set obs 6
generate g = mod(_n,2)
generate v = _n
collapse (mean) v, by(g)
display "__OPENSTAT__collapse_n=" %21.14g _N
list g v
