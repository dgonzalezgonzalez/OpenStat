* c06: regression family
clear all
set more off
set obs 20
generate x = _n
generate z = mod(_n,4)
generate y = 1 + 2*x + 0.5*z + mod(_n,3)
regress y x z
display "__OPENSTAT__b_x=" %21.14g _b[x]
display "__OPENSTAT__b_z=" %21.14g _b[z]
display "__OPENSTAT__b_cons=" %21.14g _b[_cons]
display "__OPENSTAT__se_x=" %21.14g _se[x]
display "__OPENSTAT__n=" %21.14g e(N)
display "__OPENSTAT__r2=" %21.14g e(r2)
display "__OPENSTAT__r2a=" %21.14g e(r2_a)
display "__OPENSTAT__df_r=" %21.14g e(df_r)
display "__OPENSTAT__rmse=" %21.14g e(rmse)
predict yhat
predict resid, residuals
predict xbhat, xb
summarize yhat
display "__OPENSTAT__pred_mean=" %21.14g r(mean)
test x = 0
display "__OPENSTAT__test_p=" %21.14g r(p)
estimates store m1
estimates restore m1
display "__OPENSTAT__restored_b=" %21.14g _b[x]
