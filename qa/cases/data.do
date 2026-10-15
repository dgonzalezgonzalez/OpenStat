clear all
set more off
set obs 8
generate id = _n
generate group = ceil(id/2)
generate x = id*2
replace x = . if id == 3
bysort group: egen group_mean = mean(x)
sort id
summarize x
display "__OPENSTAT__n=" %21.14g r(N)
display "__OPENSTAT__mean=" %21.14g r(mean)
display "__OPENSTAT__sd=" %21.14g r(sd)
display "__OPENSTAT__minimum=" %21.14g r(min)
display "__OPENSTAT__maximum=" %21.14g r(max)
display "__OPENSTAT__group_mean=" %21.14g group_mean[3]
count if x > 10
display "__OPENSTAT__greater_than_10=" %21.14g r(N)
count if x < .
display "__OPENSTAT__nonmissing=" %21.14g r(N)
count if x == .
display "__OPENSTAT__missing=" %21.14g r(N)
keep if id >= 3
drop if id == 8
sort x
display "__OPENSTAT__remaining=" %21.14g _N
export delimited using "data.csv", replace
