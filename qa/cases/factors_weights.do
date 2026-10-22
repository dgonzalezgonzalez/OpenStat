clear all
set more off
set obs 36
generate x = _n/10
generate g = mod(_n-1,3)
generate y = 1 + 2*x + 3*(g==1) - 2*(g==2) + .5*x*(g==1) + mod(_n,5)/10
generate w = 1 + mod(_n,4)
regress y i.g##c.x
display "__OPENSTAT__factor_x=" %21.14g _b[x]
display "__OPENSTAT__factor_g1=" %21.14g _b[1.g]
display "__OPENSTAT__factor_int=" %21.14g _b[1.g#c.x]
display "__OPENSTAT__factor_se=" %21.14g _se[1.g#c.x]
display "__OPENSTAT__factor_df=" %21.14g e(df_r)
regress y x [aw=w]
display "__OPENSTAT__aw_b=" %21.14g _b[x]
display "__OPENSTAT__aw_se=" %21.14g _se[x]
display "__OPENSTAT__aw_rss=" %21.14g e(rss)
regress y x [fw=w]
display "__OPENSTAT__fw_b=" %21.14g _b[x]
display "__OPENSTAT__fw_se=" %21.14g _se[x]
display "__OPENSTAT__fw_n=" %21.14g e(N)
display "__OPENSTAT__fw_df=" %21.14g e(df_r)
regress y x [fw=w], robust
display "__OPENSTAT__fw_robust=" %21.14g _se[x]
regress y x [pw=w]
display "__OPENSTAT__pw_b=" %21.14g _b[x]
display "__OPENSTAT__pw_se=" %21.14g _se[x]
