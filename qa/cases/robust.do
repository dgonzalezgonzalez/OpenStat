clear all
set more off
set obs 12
generate x = _n
generate z = mod(_n,3)
generate y = 1 + 2*x + 0.5*z + mod(_n,2)
regress y x z, robust
display "__OPENSTAT__b_x=" %21.14g _b[x]
display "__OPENSTAT__b_z=" %21.14g _b[z]
display "__OPENSTAT__b_cons=" %21.14g _b[_cons]
display "__OPENSTAT__se_x=" %21.14g _se[x]
display "__OPENSTAT__se_z=" %21.14g _se[z]
display "__OPENSTAT__se_cons=" %21.14g _se[_cons]
display "__OPENSTAT__n=" %21.14g e(N)
display "__OPENSTAT__df_r=" %21.14g e(df_r)
display "__OPENSTAT__r2=" %21.14g e(r2)
regress y x, vce(cluster z)
display "__OPENSTAT__cluster_b_x=" %21.14g _b[x]
display "__OPENSTAT__cluster_b_cons=" %21.14g _b[_cons]
display "__OPENSTAT__cluster_se_x=" %21.14g _se[x]
display "__OPENSTAT__cluster_se_cons=" %21.14g _se[_cons]
display "__OPENSTAT__cluster_df_r=" %21.14g e(df_r)
