clear all
set obs 3
generate x = _n
summarize x, imaginaryoption
