* Synthetic package conformance: requires reghdfe and ftools on reference ado path.
* No personal data, network calls, or package source in this fixture.
clear all
set more off
set obs 120
generate unit = ceil(_n/6)
generate time = mod(_n-1,6)+1
generate coarse = ceil(unit/4)
generate x = .15*_n + mod(_n,5)
generate z = mod(_n,11) + time*.13
generate w = mod(_n,4)+1
generate y = 1 + 1.7*x - .4*z + .25*unit - .3*time + mod(_n,7)/10
reghdfe y x z, absorb(unit time)
display "__OPENSTAT__twoway_b=" %21.14g _b[x]
display "__OPENSTAT__twoway_z=" %21.14g _b[z]
display "__OPENSTAT__twoway_cons=" %21.14g _b[_cons]
display "__OPENSTAT__twoway_se=" %21.14g _se[x]
display "__OPENSTAT__twoway_secons=" %21.14g _se[_cons]
display "__OPENSTAT__twoway_df=" %21.14g e(df_r)
display "__OPENSTAT__twoway_dfa=" %21.14g e(df_a)
display "__OPENSTAT__twoway_r2=" %21.14g e(r2)
display "__OPENSTAT__twoway_rmse=" %21.14g e(rmse)
reghdfe y x z [aw=w], absorb(unit time) vce(robust)
display "__OPENSTAT__weighted_b=" %21.14g _b[x]
display "__OPENSTAT__weighted_z=" %21.14g _b[z]
display "__OPENSTAT__weighted_cons=" %21.14g _b[_cons]
display "__OPENSTAT__weighted_se=" %21.14g _se[x]
display "__OPENSTAT__weighted_secons=" %21.14g _se[_cons]
display "__OPENSTAT__weighted_df=" %21.14g e(df_r)
display "__OPENSTAT__weighted_dfa=" %21.14g e(df_a)
display "__OPENSTAT__weighted_r2=" %21.14g e(r2)
display "__OPENSTAT__weighted_rmse=" %21.14g e(rmse)
reghdfe y x z [aw=w], absorb(unit time) vce(cluster unit)
display "__OPENSTAT__nested_b=" %21.14g _b[x]
display "__OPENSTAT__nested_cons=" %21.14g _b[_cons]
display "__OPENSTAT__nested_se=" %21.14g _se[x]
display "__OPENSTAT__nested_secons=" %21.14g _se[_cons]
display "__OPENSTAT__nested_df=" %21.14g e(df_r)
display "__OPENSTAT__nested_dfa=" %21.14g e(df_a)
display "__OPENSTAT__nested_clusters=" %21.14g e(N_clust)
display "__OPENSTAT__nested_rmse=" %21.14g e(rmse)
reghdfe y x z, absorb(unit) vce(cluster coarse)
display "__OPENSTAT__coarse_b=" %21.14g _b[x]
display "__OPENSTAT__coarse_cons=" %21.14g _b[_cons]
display "__OPENSTAT__coarse_se=" %21.14g _se[x]
display "__OPENSTAT__coarse_secons=" %21.14g _se[_cons]
display "__OPENSTAT__coarse_df=" %21.14g e(df_r)
display "__OPENSTAT__coarse_dfa=" %21.14g e(df_a)
display "__OPENSTAT__coarse_clusters=" %21.14g e(N_clust)
display "__OPENSTAT__coarse_rmse=" %21.14g e(rmse)
