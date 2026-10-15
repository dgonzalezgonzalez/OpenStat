clear all
set more off
set obs 5
generate x = _n
replace x = x[_n-1] in 2/l
display "__OPENSTAT__recursive_replace=" %21.14g x[5]
generate y = 1
replace y = y[_n-1]+1 in 2/l
display "__OPENSTAT__recursive_sequence=" %21.14g y[5]
generate flag = y >= 3
summarize flag
display "__OPENSTAT__logical_sum=" %21.14g r(sum)
display "__OPENSTAT__inrange_missing_lower=" %21.14g inrange(3,.,5)
display "__OPENSTAT__inrange_missing_value=" %21.14g inrange(.,0,.)
replace x = _n
scalar first_observation = 0
if x == 1 {
    scalar first_observation = 1
}
display "__OPENSTAT__if_first_observation=" %21.14g first_observation
generate running = sum(x) if _n > 2
display "__OPENSTAT__qualified_running_sum=" %21.14g running[4]
generate running_all = sum(1)
display "__OPENSTAT__scalar_running_sum=" %21.14g running_all[5]
local iterations 0
while `iterations' < 3 {
    local iterations = `iterations' + 1
}
display "__OPENSTAT__while_iterations=" %21.14g `iterations'
program define qa_early, rclass
    return scalar before_exit = 7
    exit 0
    return scalar before_exit = 99
end
qa_early
display "__OPENSTAT__program_exit=" %21.14g r(before_exit)
display "__OPENSTAT__caller_continues=" %21.14g 1
