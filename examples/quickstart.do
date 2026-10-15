* OpenStat quickstart; deterministic synthetic data, no private inputs.
clear all
set more off
set obs 12
generate x = _n
generate z = mod(_n,3)
generate y = 1 + 2*x + .5*z + mod(_n,2)
label variable y "Synthetic outcome"
summarize y x z
regress y x z
display "Slope: " %9.6f _b[x]
predict fitted
scatter y x, title("OpenStat synthetic example")
graph export "quickstart.png", replace width(900)
export delimited using "quickstart.csv", replace
