* c09: random numbers - the generator and the transforms must match Stata
clear all
set more off

* the uniform variate, taken one draw at a time
set seed 12345
local i 1
while `i' <= 4 {
    local u`i' = runiform()
    local i = `i' + 1
}
display "__OPENSTAT__u1=" %21.14g `u1'
display "__OPENSTAT__u2=" %21.14g `u2'
display "__OPENSTAT__u3=" %21.14g `u3'
display "__OPENSTAT__u4=" %21.14g `u4'

* the normal variate, taken one draw at a time
set seed 12345
local i 1
while `i' <= 4 {
    local n`i' = rnormal()
    local i = `i' + 1
}
display "__OPENSTAT__n1=" %21.14g `n1'
display "__OPENSTAT__n2=" %21.14g `n2'
display "__OPENSTAT__n3=" %21.14g `n3'
display "__OPENSTAT__n4=" %21.14g `n4'

* a scaled and shifted normal
set seed 871835
local a = rnormal(10, 2)
local b = rnormal(10, 2)
display "__OPENSTAT__s1=" %21.14g `a'
display "__OPENSTAT__s2=" %21.14g `b'

* a scaled and shifted uniform
set seed 871835
local c = runiform(3, 7)
local d = runiform(3, 7)
display "__OPENSTAT__t1=" %21.14g `c'
display "__OPENSTAT__t2=" %21.14g `d'

* the stream is shared: a normal draw and a uniform draw advance the same
* sequence, and a later normal draw continues where they left off
set seed 12345
local p = rnormal()
local q = runiform()
local r = rnormal()
display "__OPENSTAT__p=" %21.14g `p'
display "__OPENSTAT__q=" %21.14g `q'
display "__OPENSTAT__r=" %21.14g `r'

* generating a variable takes the draws in observation order
set seed 12345
set obs 4
gen double z = rnormal()
gen double v = runiform()
summarize z v
display "__OPENSTAT__zmean=" %21.14g `r(mean)'
display "__OPENSTAT__zsd=" %21.14g `r(sd)'
display "__OPENSTAT__vmean=" %21.14g `r(mean)'
display "__OPENSTAT__vsd=" %21.14g `r(sd)'
