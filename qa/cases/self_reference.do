clear all
set more off
set obs 5
generate y = cond(_n==1,1,y[_n-1]+1)
