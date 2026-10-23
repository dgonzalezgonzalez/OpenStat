clear all
set more off
set obs 60
generate double z = mod(_n,7) + _n/15
generate double x = mod(_n,3) + _n/20
generate double d = .6*z + .2*x + mod(_n,5)/3
generate double y = 1 + 2*d + .5*x + mod(_n,4)/5
generate g = ceil(_n/10)
ivregress 2sls y x (d=z)
display "__OPENSTAT__iv_b=" %21.14g _b[d]
display "__OPENSTAT__iv_x=" %21.14g _b[x]
display "__OPENSTAT__iv_cons=" %21.14g _b[_cons]
display "__OPENSTAT__iv_se=" %21.14g _se[d]
display "__OPENSTAT__iv_rmse=" %21.14g e(rmse)
ivregress 2sls y x (d=z), robust
display "__OPENSTAT__iv_robust=" %21.14g _se[d]
ivregress 2sls y x (d=z), small vce(cluster g)
display "__OPENSTAT__iv_cluster=" %21.14g _se[d]
display "__OPENSTAT__iv_clusterdf=" %21.14g e(df_r)
predict double fitted
predict double residual, residuals
assert abs(y-fitted-residual)<1e-10
