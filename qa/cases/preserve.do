clear all
set more off
set obs 5
generate id = _n
preserve
keep if id <= 2
display "__OPENSTAT__preserved_n=" %21.14g _N
restore
display "__OPENSTAT__restored_n=" %21.14g _N
display "__OPENSTAT__last_id=" %21.14g id[5]
