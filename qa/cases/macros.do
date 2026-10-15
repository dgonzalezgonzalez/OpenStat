clear all
set more off
local total 0
forvalues i = 1/5 {
    local total = `total' + `i'
}
global qa_shift 3
scalar total = `total' + $qa_shift
display "__OPENSTAT__total=" %21.14g total
program define qa_double, rclass
    args number
    return scalar doubled = 2 * `number'
end
qa_double 7
display "__OPENSTAT__program=" %21.14g r(doubled)
capture nonexistent_qa_command
local observed_rc = _rc
display "__OPENSTAT__captured_rc=" %21.14g `observed_rc'
local count 0
foreach word in red green blue {
    local count = `count' + 1
}
display "__OPENSTAT__foreach_count=" %21.14g `count'
