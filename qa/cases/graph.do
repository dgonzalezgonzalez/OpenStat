clear all
set more off
set obs 6
generate x = _n
generate y = x*x
scatter y x
graph export "scatter.png", replace width(640)
display "__OPENSTAT__n=" %21.14g _N
