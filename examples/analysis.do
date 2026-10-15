* Synthetic example. No external files or random-number dependence.
clear all
set obs 20
generate id = _n
generate treatment = mod(id,2)
generate x = id/10
generate y = 2 + 3*x + treatment + mod(id,3)/10
summarize y
regress y x treatment
scatter y x
graph export "analysis.png", replace
save "analysis.dta", replace
