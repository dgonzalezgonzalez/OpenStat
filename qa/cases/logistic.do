clear all
set more off
set obs 36
generate x = mod(_n-1,3)-1
generate z = mod(ceil(_n/3)-1,2)
generate y = mod(ceil(_n/6)-1,3)==0
logistic y x z
display "__OPENSTAT__b_x=" %21.14g _b[x]
display "__OPENSTAT__b_z=" %21.14g _b[z]
display "__OPENSTAT__b_cons=" %21.14g _b[_cons]
display "__OPENSTAT__se_x=" %21.14g _se[x]
display "__OPENSTAT__se_z=" %21.14g _se[z]
display "__OPENSTAT__se_cons=" %21.14g _se[_cons]
display "__OPENSTAT__n=" %21.14g e(N)
display "__OPENSTAT__ll=" %21.14g e(ll)
