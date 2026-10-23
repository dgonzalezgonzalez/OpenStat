clear all
set more off
set obs 40
generate unit = ceil(_n/5)
generate time = mod(_n-1,5)+1
generate x = .2*_n + mod(_n,3)
generate y = 1 + 2*x + .5*unit - .3*time + mod(_n,7)/10
xtset unit time
generate lagged = L.x
display "__OPENSTAT__lag=" %21.14g lagged[2]
display "__OPENSTAT__gap_missing=" %21.14g missing(lagged[6])
areg y x i.time, absorb(unit)
display "__OPENSTAT__areg_b=" %21.14g _b[x]
display "__OPENSTAT__areg_cons=" %21.14g _b[_cons]
display "__OPENSTAT__areg_se=" %21.14g _se[x]
display "__OPENSTAT__areg_secons=" %21.14g _se[_cons]
display "__OPENSTAT__areg_df=" %21.14g e(df_r)
display "__OPENSTAT__areg_r2=" %21.14g e(r2)
areg y x i.time, absorb(unit) vce(cluster unit)
display "__OPENSTAT__areg_cluster=" %21.14g _se[x]
display "__OPENSTAT__areg_clusterdf=" %21.14g e(df_r)
xtreg y x i.time, fe
display "__OPENSTAT__xtreg_b=" %21.14g _b[x]
display "__OPENSTAT__xtreg_se=" %21.14g _se[x]
display "__OPENSTAT__xtreg_df=" %21.14g e(df_r)
display "__OPENSTAT__xtreg_r2w=" %21.14g e(r2_w)
xtreg y x i.time, fe vce(cluster unit)
display "__OPENSTAT__xtreg_cluster=" %21.14g _se[x]
display "__OPENSTAT__xtreg_clusterdf=" %21.14g e(df_r)
