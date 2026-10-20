* c03: control flow - forvalues, foreach, while, if/else, programs
clear all
set more off
local total 0
forvalues i = 1/5 {
    local total = `total' + `i'
}
display "__OPENSTAT__forvalues=" %21.14g `total'
local total2 0
forvalues i = 2(2)10 {
    local total2 = `total2' + `i'
}
display "__OPENSTAT__forvalues_step=" %21.14g `total2'
local cnt 0
foreach w in a b c d {
    local cnt = `cnt' + 1
}
display "__OPENSTAT__foreach=" %21.14g `cnt'
set obs 3
generate v1 = _n
generate v2 = _n*10
generate v3 = _n*100
local nv 0
foreach v of varlist v1 v2 v3 {
    local nv = `nv' + 1
}
display "__OPENSTAT__foreach_varlist=" %21.14g `nv'
local i 0
while `i' < 5 {
    local i = `i' + 1
}
display "__OPENSTAT__while=" %21.14g `i'
if 1 == 1 {
    display "if_branch"
}
if 1 == 2 {
    display "never"
}
else {
    display "else_branch"
}
program define mysum, rclass
    args number
    return scalar doubled = 2 * `number'
end
mysum 7
display "__OPENSTAT__program=" %21.14g r(doubled)
capture nosuchcommand
local rc1 = _rc
display "__OPENSTAT__captured_rc=" %21.14g `rc1'
quietly display "quiet"
noisily display "noisy"
