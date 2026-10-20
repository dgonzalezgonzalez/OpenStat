* c04: describe/summarize/count/tabulate/correlate/list
clear all
set more off
set obs 6
generate id = _n
generate grp = mod(_n,2)
generate val = id * 1.5
label variable val "Test value"
label define grplab 0 "Even" 1 "Odd"
label values grp grplab
describe
summarize id val
summarize val, detail
count
count if id > 3
display "__OPENSTAT__count_all=" %21.14g r(N)
tabulate grp
display "__OPENSTAT__tab_n=" %21.14g r(N)
correlate id val
display "__OPENSTAT__rho=" %21.14g r(rho)
list id val in 1/3
codebook id
